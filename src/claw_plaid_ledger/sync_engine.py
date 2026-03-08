"""Sync orchestration for Plaid transaction ingestion."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from claw_plaid_ledger.db import (
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
        result = adapter.sync_transactions(access_token, cursor=cursor)

        for account in result.accounts:
            upsert_account(connection, account)

        for transaction in result.added:
            upsert_transaction(connection, transaction)

        for transaction in result.modified:
            upsert_transaction(connection, transaction)

        upsert_sync_state(
            connection,
            item_id=item_id,
            cursor=result.next_cursor,
        )
        connection.commit()

    return SyncSummary(
        added=len(result.added),
        modified=len(result.modified),
        removed=len(result.removed),
        accounts=len(result.accounts),
        next_cursor=result.next_cursor,
    )
