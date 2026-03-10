"""Tests for sync orchestration."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.db import get_all_sync_state, get_sync_cursor
from claw_plaid_ledger.plaid_models import (
    AccountData,
    RemovedTransactionData,
    SyncResult,
    TransactionData,
)
from claw_plaid_ledger.sync_engine import (
    PlaidPermanentError,
    PlaidTransientError,
    run_sync,
)

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


class FailingAdapter:
    """FakeAdapter variant that raises a configured error on a given page."""

    def __init__(
        self,
        results: tuple[SyncResult, ...],
        *,
        fail_on_page: int,
        error: Exception,
    ) -> None:
        """Configure results and the page number (0-indexed) that raises."""
        self._results = list(results)
        self._fail_on_page = fail_on_page
        self._error = error
        self._page = 0

    def sync_transactions(self, *args: object, **kwargs: object) -> SyncResult:
        """Return the next result, or raise on the configured page."""
        assert args[0] == SYNC_ACCESS_VALUE
        cursor_arg = kwargs.get("cursor") if kwargs else None
        assert cursor_arg is None or isinstance(cursor_arg, str)
        current_page = self._page
        self._page += 1
        if current_page == self._fail_on_page:
            raise self._error
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


def test_run_sync_account_count_is_distinct_across_pages(
    tmp_path: Path,
) -> None:
    """account_count is distinct accounts, not pages x accounts (BUG-001)."""
    db_path = tmp_path / "ledger.db"
    adapter = FakeAdapter(
        (
            _result("cursor-1", has_more=True),
            _result("cursor-2"),
        )
    )

    summary = run_sync(
        db_path=db_path,
        adapter=adapter,
        access_token=SYNC_ACCESS_VALUE,
    )

    # Both pages return the same single account; summary must report 1, not 2.
    assert summary.accounts == 1


def test_run_sync_uses_custom_item_id(tmp_path: Path) -> None:
    """run_sync stores and reads sync state under the supplied item_id."""
    db_path = tmp_path / "ledger.db"
    custom_item_id = "my-bank-item"
    adapter = FakeAdapter((_result("cursor-custom"),))

    summary = run_sync(
        db_path=db_path,
        adapter=adapter,
        access_token=SYNC_ACCESS_VALUE,
        item_id=custom_item_id,
    )

    assert summary.next_cursor == "cursor-custom"

    with sqlite3.connect(db_path) as connection:
        # Cursor is stored under the custom item_id, not the default.
        assert get_sync_cursor(connection, custom_item_id) == "cursor-custom"
        assert get_sync_cursor(connection, "default-item") is None


def test_run_sync_item_id_isolation(tmp_path: Path) -> None:
    """Separate item_ids maintain independent cursors in the same DB."""
    db_path = tmp_path / "ledger.db"

    adapter_a = FakeAdapter((_result("cursor-a"),))
    run_sync(
        db_path=db_path,
        adapter=adapter_a,
        access_token=SYNC_ACCESS_VALUE,
        item_id="item-a",
    )

    adapter_b = FakeAdapter((_result("cursor-b"),))
    run_sync(
        db_path=db_path,
        adapter=adapter_b,
        access_token=SYNC_ACCESS_VALUE,
        item_id="item-b",
    )

    with sqlite3.connect(db_path) as connection:
        assert get_sync_cursor(connection, "item-a") == "cursor-a"
        assert get_sync_cursor(connection, "item-b") == "cursor-b"


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


def test_run_sync_preserves_cursor_on_first_page_exception(
    tmp_path: Path,
) -> None:
    """A transient error on page 1 leaves the DB clean and cursor unchanged."""
    db_path = tmp_path / "ledger.db"
    adapter = FailingAdapter(
        (),
        fail_on_page=0,
        error=PlaidTransientError("network blip"),
    )

    with pytest.raises(PlaidTransientError, match="network blip"):
        run_sync(
            db_path=db_path,
            adapter=adapter,
            access_token=SYNC_ACCESS_VALUE,
        )

    with sqlite3.connect(db_path) as connection:
        # No transactions or cursor state should have been written.
        tx_count = connection.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        assert tx_count == 0
        assert get_sync_cursor(connection, "default-item") is None


def test_run_sync_preserves_prior_cursor_on_mid_loop_exception(
    tmp_path: Path,
) -> None:
    """A transient error on page 2 rolls back writes; prior cursor survives."""
    db_path = tmp_path / "ledger.db"

    # First sync succeeds and establishes a cursor.
    first = FakeAdapter((_result("cursor-1"),))
    run_sync(
        db_path=db_path,
        adapter=first,
        access_token=SYNC_ACCESS_VALUE,
    )

    # Second sync: page 1 succeeds (has_more=True), page 2 raises.
    second = FailingAdapter(
        (_result("cursor-2", has_more=True),),
        fail_on_page=1,
        error=PlaidTransientError("rate limit"),
    )

    with pytest.raises(PlaidTransientError, match="rate limit"):
        run_sync(
            db_path=db_path,
            adapter=second,
            access_token=SYNC_ACCESS_VALUE,
        )

    with sqlite3.connect(db_path) as connection:
        # Cursor must still be from the first successful run.
        assert get_sync_cursor(connection, "default-item") == "cursor-1"
        # Only the one transaction from the first run should exist.
        tx_count = connection.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        assert tx_count == 1


def test_run_sync_wraps_unknown_exception_as_transient(tmp_path: Path) -> None:
    """An unexpected adapter exception is wrapped as PlaidTransientError."""
    db_path = tmp_path / "ledger.db"
    adapter = FailingAdapter(
        (),
        fail_on_page=0,
        error=ValueError("malformed response"),
    )

    with pytest.raises(PlaidTransientError, match="malformed response"):
        run_sync(
            db_path=db_path,
            adapter=adapter,
            access_token=SYNC_ACCESS_VALUE,
        )


def test_run_sync_propagates_permanent_error(tmp_path: Path) -> None:
    """A PlaidPermanentError propagates unchanged from run_sync."""
    db_path = tmp_path / "ledger.db"
    adapter = FailingAdapter(
        (),
        fail_on_page=0,
        error=PlaidPermanentError("invalid access token"),
    )

    with pytest.raises(PlaidPermanentError, match="invalid access token"):
        run_sync(
            db_path=db_path,
            adapter=adapter,
            access_token=SYNC_ACCESS_VALUE,
        )


def test_run_sync_persists_owner_when_provided(tmp_path: Path) -> None:
    """run_sync stores owner on sync_state and accounts when provided."""
    db_path = tmp_path / "ledger.db"
    adapter = FakeAdapter((_result("cursor-owner"),))

    run_sync(
        db_path=db_path,
        adapter=adapter,
        access_token=SYNC_ACCESS_VALUE,
        item_id="item-owner",
        owner="bob",
    )

    with sqlite3.connect(db_path) as connection:
        sync_rows = get_all_sync_state(connection)
        assert len(sync_rows) == 1
        assert sync_rows[0].item_id == "item-owner"
        assert sync_rows[0].owner == "bob"
        assert sync_rows[0].last_synced_at is not None
        account_owner = connection.execute(
            "SELECT owner FROM accounts WHERE plaid_account_id = ?",
            ("acct-1",),
        ).fetchone()[0]
        assert account_owner == "bob"


def test_run_sync_owner_defaults_to_none(tmp_path: Path) -> None:
    """run_sync stores null owner when owner argument is omitted."""
    db_path = tmp_path / "ledger.db"
    adapter = FakeAdapter((_result("cursor-none"),))

    run_sync(
        db_path=db_path,
        adapter=adapter,
        access_token=SYNC_ACCESS_VALUE,
        item_id="item-none",
    )

    with sqlite3.connect(db_path) as connection:
        sync_rows = get_all_sync_state(connection)
        assert len(sync_rows) == 1
        assert sync_rows[0].item_id == "item-none"
        assert sync_rows[0].owner is None
        assert sync_rows[0].last_synced_at is not None
        account_owner = connection.execute(
            "SELECT owner FROM accounts WHERE plaid_account_id = ?",
            ("acct-1",),
        ).fetchone()[0]
        assert account_owner is None
