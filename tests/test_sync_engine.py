"""Tests for sync orchestration."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import TYPE_CHECKING

from claw_plaid_ledger.db import get_sync_cursor
from claw_plaid_ledger.plaid_models import (
    AccountData,
    RemovedTransactionData,
    SyncResult,
    TransactionData,
)
from claw_plaid_ledger.sync_engine import run_sync

if TYPE_CHECKING:
    from pathlib import Path

SYNC_ACCESS_VALUE = "integration-fixture"
UPDATED_AMOUNT = 2.0


class FakeAdapter:
    """Deterministic adapter returning prebuilt sync results."""

    def __init__(self, results: tuple[SyncResult, ...]) -> None:
        """Store deterministic sync results for test orchestration."""
        self._results = list(results)
        self.received_cursors: list[str | None] = []

    def sync_transactions(self, *args: object, **kwargs: object) -> SyncResult:
        """Record cursor usage and return the next configured result."""
        assert args[0] == SYNC_ACCESS_VALUE
        cursor_arg = kwargs.get("cursor") if kwargs else None
        assert cursor_arg is None or isinstance(cursor_arg, str)
        self.received_cursors.append(cursor_arg)
        return self._results.pop(0)


def _result(
    next_cursor: str,
    *,
    amount: float = 12.5,
    has_more: bool = False,
    removed: tuple[RemovedTransactionData, ...] = (),
) -> SyncResult:
    """Build a small SyncResult payload for test fixtures."""
    return SyncResult(
        accounts=(
            AccountData(
                plaid_account_id="acct-1",
                name="Checking",
                type="depository",
                subtype="checking",
                mask="1234",
            ),
        ),
        added=(
            TransactionData(
                plaid_transaction_id="tx-1",
                plaid_account_id="acct-1",
                amount=amount,
                date=date(2024, 1, 1),
                name="Coffee",
                pending=False,
                merchant_name="Cafe",
                iso_currency_code="USD",
            ),
        ),
        modified=(),
        removed=removed,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def test_run_sync_persists_rows_and_cursor(tmp_path: Path) -> None:
    """run_sync writes transactions/accounts and updates sync state."""
    db_path = tmp_path / "ledger.db"
    adapter = FakeAdapter((_result("cursor-1"),))

    summary = run_sync(
        db_path=db_path,
        adapter=adapter,
        access_token=SYNC_ACCESS_VALUE,
    )

    assert summary.accounts == 1
    assert summary.added == 1
    assert summary.next_cursor == "cursor-1"
    assert adapter.received_cursors == [None]

    with sqlite3.connect(db_path) as connection:
        tx_count = connection.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        assert tx_count == 1
        assert get_sync_cursor(connection, "default-item") == "cursor-1"


def test_run_sync_reads_prior_cursor(tmp_path: Path) -> None:
    """run_sync resumes from the previously stored cursor."""
    db_path = tmp_path / "ledger.db"

    first = FakeAdapter((_result("cursor-1", amount=1.0),))
    run_sync(
        db_path=db_path,
        adapter=first,
        access_token=SYNC_ACCESS_VALUE,
    )

    second = FakeAdapter((_result("cursor-2", amount=UPDATED_AMOUNT),))
    run_sync(
        db_path=db_path,
        adapter=second,
        access_token=SYNC_ACCESS_VALUE,
    )

    assert second.received_cursors == ["cursor-1"]

    with sqlite3.connect(db_path) as connection:
        value = connection.execute(
            "SELECT amount FROM transactions WHERE plaid_transaction_id = ?",
            ("tx-1",),
        ).fetchone()[0]
        assert value == UPDATED_AMOUNT


def test_run_sync_pages_until_has_more_is_false(tmp_path: Path) -> None:
    """run_sync continues fetching pages until Plaid reports completion."""
    db_path = tmp_path / "ledger.db"
    adapter = FakeAdapter(
        (
            _result("cursor-1", has_more=True),
            _result("cursor-2", amount=13.5),
        )
    )

    summary = run_sync(
        db_path=db_path,
        adapter=adapter,
        access_token=SYNC_ACCESS_VALUE,
    )

    assert adapter.received_cursors == [None, "cursor-1"]
    assert summary.next_cursor == "cursor-2"

    with sqlite3.connect(db_path) as connection:
        assert get_sync_cursor(connection, "default-item") == "cursor-2"


def test_run_sync_deletes_removed_transactions(tmp_path: Path) -> None:
    """run_sync removes transactions that Plaid marks as removed."""
    db_path = tmp_path / "ledger.db"

    first = FakeAdapter((_result("cursor-1"),))
    run_sync(
        db_path=db_path,
        adapter=first,
        access_token=SYNC_ACCESS_VALUE,
    )

    second = FakeAdapter(
        (
            _result(
                "cursor-2",
                amount=UPDATED_AMOUNT,
                removed=(RemovedTransactionData(plaid_transaction_id="tx-1"),),
            ),
        )
    )
    summary = run_sync(
        db_path=db_path,
        adapter=second,
        access_token=SYNC_ACCESS_VALUE,
    )

    assert summary.removed == 1

    with sqlite3.connect(db_path) as connection:
        tx_count = connection.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        assert tx_count == 0
