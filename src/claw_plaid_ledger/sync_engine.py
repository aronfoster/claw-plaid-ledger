"""Sync orchestration for Plaid transaction ingestion."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from claw_plaid_ledger.db import (
    delete_transaction,
    get_sync_cursor,
    initialize_database,
    upsert_account,
    upsert_sync_state,
    upsert_transaction,
)

if TYPE_CHECKING:
    from pathlib import Path

    from claw_plaid_ledger.plaid_models import SyncResult


class SyncAdapter(Protocol):
    """Structural interface required by run_sync."""

    def sync_transactions(
        self,
        access_token: str,
        cursor: str | None = None,
    ) -> SyncResult:
        """Fetch one sync page from Plaid."""


DEFAULT_ITEM_ID = "default-item"


@dataclass(frozen=True)
class SyncSummary:
    """Concise summary of a sync run for operator output."""

    added: int
    modified: int
    removed: int
    accounts: int
    next_cursor: str


def run_sync(
    *,
    db_path: Path,
    adapter: SyncAdapter,
    access_token: str,
    item_id: str = DEFAULT_ITEM_ID,
) -> SyncSummary:
    """Run one sync cycle and persist the result into SQLite."""
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        cursor = get_sync_cursor(connection, item_id)
        added_count = 0
        modified_count = 0
        removed_count = 0
        seen_account_ids: set[str] = set()

        while True:
            result = adapter.sync_transactions(access_token, cursor=cursor)

            for account in result.accounts:
                upsert_account(connection, account)
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

        upsert_sync_state(
            connection,
            item_id=item_id,
            cursor=cursor,
        )
        connection.commit()

    return SyncSummary(
        added=added_count,
        modified=modified_count,
        removed=removed_count,
        accounts=len(seen_account_ids),
        next_cursor=cursor or "",
    )
