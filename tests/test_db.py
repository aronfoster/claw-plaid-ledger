"""Database bootstrap and schema constraint tests."""

from __future__ import annotations

import datetime
import sqlite3
from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.db import (
    AnnotationRow,
    get_annotation,
    get_sync_cursor,
    initialize_database,
    normalize_account_for_db,
    normalize_transaction_for_db,
    upsert_account,
    upsert_annotation,
    upsert_sync_state,
    upsert_transaction,
)
from claw_plaid_ledger.plaid_models import AccountData, TransactionData

TRANSACTION_INSERT_SQL = (
    "INSERT INTO transactions "
    "(plaid_transaction_id, plaid_account_id, amount, name, pending, "
    "created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

if TYPE_CHECKING:
    from pathlib import Path


REQUIRED_TABLES = {"accounts", "transactions", "sync_state", "annotations"}


def test_initialize_database_creates_file_and_tables(tmp_path: Path) -> None:
    """Initialization creates the DB file and required tables."""
    db_path = tmp_path / "ledger" / "ledger.db"

    initialize_database(db_path)

    assert db_path.exists()

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    table_names = {name for (name,) in rows}
    assert REQUIRED_TABLES.issubset(table_names)


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    """Initialization can run repeatedly without errors."""
    db_path = tmp_path / "ledger.db"

    initialize_database(db_path)
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    table_names = {name for (name,) in rows}
    assert REQUIRED_TABLES.issubset(table_names)


def test_schema_enforces_required_unique_identifiers(tmp_path: Path) -> None:
    """Schema rejects duplicate upstream IDs used as stable identifiers."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            (
                "INSERT INTO accounts "
                "(plaid_account_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)"
            ),
            (
                "acct_123",
                "Checking",
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                (
                    "INSERT INTO accounts "
                    "(plaid_account_id, name, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)"
                ),
                (
                    "acct_123",
                    "Savings",
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

        connection.execute(
            TRANSACTION_INSERT_SQL,
            (
                "txn_123",
                "acct_123",
                "15.25",
                "Coffee",
                0,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                TRANSACTION_INSERT_SQL,
                (
                    "txn_123",
                    "acct_123",
                    "23.00",
                    "Lunch",
                    0,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

        connection.execute(
            (
                "INSERT INTO sync_state "
                "(item_id, cursor, last_synced_at) "
                "VALUES (?, ?, ?)"
            ),
            ("item_123", "cursor_a", "2024-01-02T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                (
                    "INSERT INTO sync_state "
                    "(item_id, cursor, last_synced_at) "
                    "VALUES (?, ?, ?)"
                ),
                ("item_123", "cursor_b", "2024-01-03T00:00:00Z"),
            )


def test_schema_enforces_required_not_null_columns(tmp_path: Path) -> None:
    """Schema rejects NULL values in required columns."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                (
                    "INSERT INTO accounts "
                    "(plaid_account_id, name, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)"
                ),
                (
                    "acct_null",
                    None,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                TRANSACTION_INSERT_SQL,
                (
                    "txn_null",
                    "acct_missing_name",
                    "1.00",
                    "Name is present",
                    None,
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z",
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                (
                    "INSERT INTO sync_state "
                    "(item_id, cursor, last_synced_at) "
                    "VALUES (?, ?, ?)"
                ),
                (None, "cursor", "2024-01-01T00:00:00Z"),
            )


# ---------------------------------------------------------------------------
# Sync-oriented DB helper behavior
# ---------------------------------------------------------------------------


def _account(
    *, name: str = "Checking", mask: str | None = "1234"
) -> AccountData:
    """Build a representative account fixture for DB helper tests."""
    return AccountData(
        plaid_account_id="acc-1",
        name=name,
        type="depository",
        subtype="checking",
        mask=mask,
    )


def _transaction(
    *,
    tx_id: str = "tx-1",
    amount: float = 10.25,
    date: datetime.date = datetime.date(2024, 5, 1),
    pending: bool = False,
) -> TransactionData:
    """Build a representative transaction fixture for DB helper tests."""
    return TransactionData(
        plaid_transaction_id=tx_id,
        plaid_account_id="acc-1",
        amount=amount,
        date=date,
        name="Coffee",
        pending=pending,
        merchant_name="Bean Co",
        iso_currency_code="USD",
    )


def test_normalize_account_for_db() -> None:
    """Account normalization maps typed fields into DB row fields."""
    row = normalize_account_for_db(_account(), institution_name="Bank")
    assert row.plaid_account_id == "acc-1"
    assert row.institution_name == "Bank"


def test_normalize_transaction_for_db_pending_false_sets_posted_date() -> None:
    """Posted transactions write posted_date and no authorized_date."""
    row = normalize_transaction_for_db(_transaction(pending=False))
    assert row.pending == 0
    assert row.authorized_date is None
    assert row.posted_date == "2024-05-01"


def test_normalize_transaction_for_db_pending_true_sets_authorized_date() -> (
    None
):
    """Pending transactions write authorized_date and no posted_date."""
    row = normalize_transaction_for_db(_transaction(pending=True))
    assert row.pending == 1
    assert row.authorized_date == "2024-05-01"
    assert row.posted_date is None


def test_upsert_account_inserts_then_updates(tmp_path: Path) -> None:
    """Account upserts update existing rows instead of duplicating them."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        upsert_account(
            connection,
            _account(name="Checking"),
            now_iso="2024-01-01T00:00:00+00:00",
        )
        upsert_account(
            connection,
            _account(name="Primary Checking", mask=None),
            institution_name="Plaid Bank",
            now_iso="2024-01-02T00:00:00+00:00",
        )

        rows = connection.execute(
            "SELECT plaid_account_id, name, mask, institution_name, "
            "created_at, updated_at FROM accounts"
        ).fetchall()

    assert len(rows) == 1
    (
        plaid_account_id,
        name,
        mask,
        institution_name,
        created_at,
        updated_at,
    ) = rows[0]
    assert plaid_account_id == "acc-1"
    assert name == "Primary Checking"
    assert mask is None
    assert institution_name == "Plaid Bank"
    assert created_at == "2024-01-01T00:00:00+00:00"
    assert updated_at == "2024-01-02T00:00:00+00:00"


def test_upsert_transaction_inserts_then_updates(tmp_path: Path) -> None:
    """Transaction upserts update existing rows instead of duplicating them."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        upsert_transaction(
            connection,
            _transaction(amount=12.00),
            now_iso="2024-01-01T00:00:00+00:00",
        )
        upsert_transaction(
            connection,
            _transaction(amount=18.50, pending=True),
            now_iso="2024-01-02T00:00:00+00:00",
        )
        rows = connection.execute(
            "SELECT plaid_transaction_id, amount, pending, authorized_date, "
            "posted_date, created_at, updated_at FROM transactions"
        ).fetchall()

    assert len(rows) == 1
    (
        tx_id,
        amount,
        pending,
        authorized_date,
        posted_date,
        created_at,
        updated_at,
    ) = rows[0]
    assert tx_id == "tx-1"
    assert amount == pytest.approx(18.5)
    assert pending == 1
    assert authorized_date == "2024-05-01"
    assert posted_date is None
    assert created_at == "2024-01-01T00:00:00+00:00"
    assert updated_at == "2024-01-02T00:00:00+00:00"


def test_sync_state_round_trip_and_rerun(tmp_path: Path) -> None:
    """Sync state writes are idempotent and the cursor is readable."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        assert get_sync_cursor(connection, "item-1") is None

        upsert_sync_state(
            connection,
            item_id="item-1",
            cursor="cursor-a",
            last_synced_at="2024-01-01T00:00:00+00:00",
        )
        assert get_sync_cursor(connection, "item-1") == "cursor-a"

        upsert_sync_state(
            connection,
            item_id="item-1",
            cursor="cursor-b",
            last_synced_at="2024-01-02T00:00:00+00:00",
        )

        rows = connection.execute(
            "SELECT item_id, cursor, last_synced_at FROM sync_state"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0] == ("item-1", "cursor-b", "2024-01-02T00:00:00+00:00")


def test_upsert_annotation_inserts_and_round_trips(tmp_path: Path) -> None:
    """Annotation upsert inserts a new row with all fields preserved."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            TRANSACTION_INSERT_SQL,
            (
                "txn_annotated",
                "acct_123",
                "15.25",
                "Coffee",
                0,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        row = AnnotationRow(
            plaid_transaction_id="txn_annotated",
            category="food",
            note="Morning coffee",
            tags='["discretionary", "recurring"]',
            created_at="2024-01-01T01:00:00+00:00",
            updated_at="2024-01-01T01:00:00+00:00",
        )
        upsert_annotation(connection, row)

        stored = get_annotation(connection, "txn_annotated")

    assert stored is not None
    assert stored == row


def test_upsert_annotation_updates_and_preserves_created_at(
    tmp_path: Path,
) -> None:
    """Updating an annotation preserves created_at and updates fields."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            TRANSACTION_INSERT_SQL,
            (
                "txn_annotated",
                "acct_123",
                "15.25",
                "Coffee",
                0,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        upsert_annotation(
            connection,
            AnnotationRow(
                plaid_transaction_id="txn_annotated",
                category="food",
                note="Morning coffee",
                tags='["discretionary"]',
                created_at="2024-01-01T01:00:00+00:00",
                updated_at="2024-01-01T01:00:00+00:00",
            ),
        )
        upsert_annotation(
            connection,
            AnnotationRow(
                plaid_transaction_id="txn_annotated",
                category="dining",
                note="Lunch",
                tags='["team"]',
                created_at="2099-01-01T00:00:00+00:00",
                updated_at="2024-01-02T01:00:00+00:00",
            ),
        )

        stored = get_annotation(connection, "txn_annotated")

    assert stored is not None
    assert stored.category == "dining"
    assert stored.note == "Lunch"
    assert stored.tags == '["team"]'
    assert stored.created_at == "2024-01-01T01:00:00+00:00"
    assert stored.updated_at == "2024-01-02T01:00:00+00:00"


def test_get_annotation_returns_none_for_missing_transaction_id(
    tmp_path: Path,
) -> None:
    """Fetching an unknown annotation returns None."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        assert get_annotation(connection, "unknown_txn") is None
