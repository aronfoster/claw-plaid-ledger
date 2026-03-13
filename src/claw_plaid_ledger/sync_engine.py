"""Sync orchestration for Plaid transaction ingestion."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from claw_plaid_ledger.db import (
    delete_transaction,
    get_sync_cursor,
    initialize_database,
    normalize_account_for_db,
    upsert_account,
    upsert_sync_state,
    upsert_transaction,
)
from claw_plaid_ledger.logging_utils import (
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

if TYPE_CHECKING:
    from pathlib import Path

    from claw_plaid_ledger.plaid_models import SyncResult


class PlaidSyncError(RuntimeError):
    """Base class for all Plaid sync errors raised by run_sync."""


class PlaidTransientError(PlaidSyncError):
    """
    Transient Plaid error (network blip, rate-limit, server failure).

    The operation may succeed on retry.  When this propagates from
    run_sync the sqlite3 context manager rolls back all in-flight writes;
    the prior cursor is preserved and the next run restarts cleanly.
    """


class PlaidPermanentError(PlaidSyncError):
    """
    Permanent Plaid error (invalid token, unsupported operation).

    Retrying without operator intervention will not succeed.
    """


class SyncAdapter(Protocol):
    """Structural interface required by run_sync."""

    def sync_transactions(
        self,
        access_token: str,
        cursor: str | None = None,
    ) -> SyncResult:
        """Fetch one sync page from Plaid."""


DEFAULT_ITEM_ID = "default-item"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncSummary:
    """Concise summary of a sync run for operator output."""

    added: int
    modified: int
    removed: int
    accounts: int
    next_cursor: str


def _sync_pages(
    *,
    connection: sqlite3.Connection,
    adapter: SyncAdapter,
    access_token: str,
    item_id: str,
    owner: str | None,
) -> SyncSummary:
    """
    Fetch all sync pages from Plaid and persist them inside *connection*.

    Raises a :class:`PlaidSyncError` subclass on any Plaid failure.
    Caller is responsible for committing or rolling back the connection.
    """
    cursor = get_sync_cursor(connection, item_id)
    added_count = 0
    modified_count = 0
    removed_count = 0
    seen_account_ids: set[str] = set()

    while True:
        # Any exception raised here propagates out of the with-block,
        # causing the sqlite3 context manager to call rollback().
        # Classify known Plaid error types and wrap unknowns as
        # transient so callers can reason about retry behavior.
        try:
            result = adapter.sync_transactions(access_token, cursor=cursor)
        except PlaidSyncError:
            # Already a classified error — re-raise and let rollback fire.
            raise
        except Exception as exc:
            # Unexpected exception from the adapter; treat as transient
            # so the operator can retry without manual intervention.
            msg = (
                "Unexpected error from Plaid adapter"
                f" (treating as transient): {exc}"
            )
            raise PlaidTransientError(msg) from exc

        for account in result.accounts:
            upsert_account(
                connection,
                normalize_account_for_db(
                    account, owner=owner, item_id=item_id
                ),
            )
            seen_account_ids.add(account.plaid_account_id)

        for transaction in result.added:
            upsert_transaction(connection, transaction)
        added_count += len(result.added)

        for transaction in result.modified:
            upsert_transaction(connection, transaction)
        modified_count += len(result.modified)

        for removed_transaction in result.removed:
            delete_transaction(
                connection,
                plaid_transaction_id=removed_transaction.plaid_transaction_id,
            )
        removed_count += len(result.removed)

        cursor = result.next_cursor
        if not result.has_more:
            break

    # Cursor-write-after-success invariant: cursor and sync state are
    # persisted only after every page has been fetched without error.
    # Any exception raised inside this with-block causes the sqlite3
    # context manager to call connection.rollback(), discarding all
    # in-flight account and transaction writes.  The prior cursor is
    # preserved, so the next run restarts from the last known-good point.
    upsert_sync_state(
        connection,
        item_id=item_id,
        cursor=cursor,
        owner=owner,
    )
    connection.commit()

    return SyncSummary(
        added=added_count,
        modified=modified_count,
        removed=removed_count,
        accounts=len(seen_account_ids),
        next_cursor=cursor or "",
    )


def run_sync(
    *,
    db_path: Path,
    adapter: SyncAdapter,
    access_token: str,
    item_id: str = DEFAULT_ITEM_ID,
    owner: str | None = None,
) -> SyncSummary:
    """
    Run one sync cycle and persist the result into SQLite.

    Every run is identified by a ``sync_run_id`` that appears in all log
    lines.  When a caller has already set a correlation ID (e.g.
    ``_background_sync`` or the CLI sync helpers), that ID is reused so the
    full request → sync chain is traceable.  When no context is active, a
    new ``sync-<hex8>`` ID is generated and set for the duration of the run.
    """
    existing_id = get_correlation_id()
    if existing_id != "-":
        sync_run_id = existing_id
        ctx_token = None
    else:
        sync_run_id = "sync-" + uuid.uuid4().hex[:8]
        ctx_token = set_correlation_id(sync_run_id)

    try:
        logger.info(
            "sync starting item_id=%s sync_run_id=%s", item_id, sync_run_id
        )
        initialize_database(db_path)
        with sqlite3.connect(db_path) as connection:
            summary = _sync_pages(
                connection=connection,
                adapter=adapter,
                access_token=access_token,
                item_id=item_id,
                owner=owner,
            )
        logger.info(
            "sync completed item_id=%s added=%d modified=%d removed=%d"
            " accounts=%d sync_run_id=%s",
            item_id,
            summary.added,
            summary.modified,
            summary.removed,
            summary.accounts,
            sync_run_id,
        )
        return summary
    finally:
        if ctx_token is not None:
            reset_correlation_id(ctx_token)
