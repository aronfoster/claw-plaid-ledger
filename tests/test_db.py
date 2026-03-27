"""Database bootstrap and schema constraint tests."""

from __future__ import annotations

import datetime
import sqlite3
from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.db import (
    AllocationRow,
    AnnotationRow,
    LedgerErrorQuery,
    LedgerErrorRow,
    NormalizedAccountRow,
    SyncStateRow,
    TransactionQuery,
    apply_account_precedence,
    get_all_sync_state,
    get_allocations_for_transaction,
    get_annotation,
    get_sync_cursor,
    get_transaction,
    initialize_database,
    insert_ledger_error,
    normalize_account_for_db,
    normalize_transaction_for_db,
    query_ledger_errors,
    query_transactions,
    replace_allocations,
    upsert_account,
    upsert_annotation,
    upsert_single_allocation,
    upsert_sync_state,
    upsert_transaction,
)
from claw_plaid_ledger.items_config import ItemConfig, SuppressedAccountConfig
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
            normalize_account_for_db(_account(name="Checking")),
            now_iso="2024-01-01T00:00:00+00:00",
        )
        upsert_account(
            connection,
            normalize_account_for_db(
                _account(name="Primary Checking", mask=None),
                institution_name="Plaid Bank",
            ),
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


def _insert_transaction_row(
    connection: sqlite3.Connection,
    row: dict[str, str | float | int | None],
) -> None:
    connection.execute(
        (
            "INSERT OR IGNORE INTO accounts ("
            "plaid_account_id, name, created_at, updated_at"
            ") VALUES (?, ?, ?, ?)"
        ),
        (
            row["account_id"],
            f"Account {row['account_id']}",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00+00:00",
        ),
    )
    connection.execute(
        (
            "INSERT INTO transactions ("
            "plaid_transaction_id, plaid_account_id, amount, "
            "iso_currency_code, name, merchant_name, pending, "
            "authorized_date, posted_date, raw_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ),
        (
            row["tx_id"],
            row["account_id"],
            row["amount"],
            "USD",
            row["name"],
            row["merchant_name"],
            row["pending"],
            row["authorized_date"],
            row["posted_date"],
            None,
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00+00:00",
        ),
    )


def test_query_transactions_filters_and_paginates(tmp_path: Path) -> None:
    """Query helper applies filters and returns unpaginated matching total."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        _insert_transaction_row(
            connection,
            {
                "tx_id": "tx_1",
                "account_id": "acct_1",
                "amount": 12.5,
                "name": "STARBUCKS #123",
                "merchant_name": "Starbucks",
                "pending": 0,
                "authorized_date": None,
                "posted_date": "2024-01-15",
            },
        )
        _insert_transaction_row(
            connection,
            {
                "tx_id": "tx_2",
                "account_id": "acct_2",
                "amount": 45.0,
                "name": "GROCERY STORE",
                "merchant_name": "Whole Foods",
                "pending": 1,
                "authorized_date": "2024-01-20",
                "posted_date": None,
            },
        )
        _insert_transaction_row(
            connection,
            {
                "tx_id": "tx_3",
                "account_id": "acct_1",
                "amount": 70.0,
                "name": "DINNER",
                "merchant_name": "Fancy Steakhouse",
                "pending": 0,
                "authorized_date": None,
                "posted_date": "2024-02-01",
            },
        )

        rows, total = query_transactions(
            connection,
            TransactionQuery(
                start_date="2024-01-10",
                end_date="2024-01-31",
                account_id="acct_1",
                pending=False,
                min_amount=10.0,
                max_amount=20.0,
                keyword="star",
                limit=10,
                offset=0,
            ),
        )

        page_rows, page_total = query_transactions(
            connection,
            TransactionQuery(limit=1, offset=1),
        )

    assert total == 1
    assert len(rows) == 1
    assert rows[0] == {
        "id": "tx_1",
        "account_id": "acct_1",
        "amount": 12.5,
        "iso_currency_code": "USD",
        "name": "STARBUCKS #123",
        "merchant_name": "Starbucks",
        "pending": False,
        "date": "2024-01-15",
        "allocation": None,
    }

    expected_total = 3
    assert page_total == expected_total
    assert len(page_rows) == 1
    assert page_rows[0]["id"] == "tx_2"


def test_query_transactions_unknown_filter_returns_empty(
    tmp_path: Path,
) -> None:
    """Query helper returns empty list and zero total for no matches."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        rows, total = query_transactions(
            connection,
            TransactionQuery(account_id="missing"),
        )

    assert rows == []
    assert total == 0


def test_query_transactions_canonical_filter_and_raw_opt_out(
    tmp_path: Path,
) -> None:
    """Canonical view excludes suppressed accounts; raw view returns all."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        _insert_transaction_row(
            connection,
            {
                "tx_id": "tx_canonical",
                "account_id": "acct_keep",
                "amount": 5.0,
                "name": "Keep",
                "merchant_name": "Keep",
                "pending": 0,
                "authorized_date": None,
                "posted_date": "2024-01-01",
            },
        )
        _insert_transaction_row(
            connection,
            {
                "tx_id": "tx_suppressed",
                "account_id": "acct_hide",
                "amount": 6.0,
                "name": "Hide",
                "merchant_name": "Hide",
                "pending": 0,
                "authorized_date": None,
                "posted_date": "2024-01-02",
            },
        )
        connection.execute(
            (
                "UPDATE accounts SET canonical_account_id = ? "
                "WHERE plaid_account_id = ?"
            ),
            ("acct_keep", "acct_hide"),
        )

        canonical_rows, canonical_total = query_transactions(
            connection,
            TransactionQuery(),
        )
        raw_rows, raw_total = query_transactions(
            connection,
            TransactionQuery(canonical_only=False),
        )

    assert canonical_total == 1
    assert [row["id"] for row in canonical_rows] == ["tx_canonical"]
    expected_raw_total = 2
    assert raw_total == expected_raw_total
    assert {row["id"] for row in raw_rows} == {"tx_canonical", "tx_suppressed"}


def test_get_transaction_returns_full_detail_row(tmp_path: Path) -> None:
    """Detail query returns a mapped transaction row including raw_json."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        _insert_transaction_row(
            connection,
            {
                "tx_id": "tx_detail",
                "account_id": "acct_1",
                "amount": 9.99,
                "name": "Cafe",
                "merchant_name": "Cafe Merchant",
                "pending": 0,
                "authorized_date": None,
                "posted_date": "2024-02-02",
            },
        )
        connection.execute(
            (
                "UPDATE transactions SET raw_json = ? "
                "WHERE plaid_transaction_id = ?"
            ),
            ('{"foo": "bar"}', "tx_detail"),
        )

        row = get_transaction(connection, "tx_detail")

    assert row == {
        "id": "tx_detail",
        "account_id": "acct_1",
        "amount": 9.99,
        "iso_currency_code": "USD",
        "name": "Cafe",
        "merchant_name": "Cafe Merchant",
        "pending": False,
        "date": "2024-02-02",
        "raw_json": '{"foo": "bar"}',
        "suppressed_by": None,
    }


def test_get_transaction_returns_suppression_provenance(
    tmp_path: Path,
) -> None:
    """Detail query includes suppressed_by for suppressed accounts."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        _insert_transaction_row(
            connection,
            {
                "tx_id": "tx_suppressed_detail",
                "account_id": "acct_2",
                "amount": 2.5,
                "name": "Suppressed",
                "merchant_name": "Suppressed",
                "pending": 0,
                "authorized_date": None,
                "posted_date": "2024-02-03",
            },
        )
        connection.execute(
            (
                "UPDATE accounts SET canonical_account_id = ? "
                "WHERE plaid_account_id = ?"
            ),
            ("acct_1", "acct_2"),
        )

        row = get_transaction(connection, "tx_suppressed_detail")

    assert row is not None
    assert row["suppressed_by"] == "acct_1"


def test_get_transaction_returns_none_for_missing_id(tmp_path: Path) -> None:
    """Detail query returns None when transaction id is unknown."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        row = get_transaction(connection, "missing")

    assert row is None


# ---------------------------------------------------------------------------
# Task 1: owner column migration and helpers
# ---------------------------------------------------------------------------


def _account_column_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(accounts)").fetchall()
    return {row[1] for row in rows}


def _sync_state_column_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(sync_state)").fetchall()
    return {row[1] for row in rows}


def test_initialize_database_fresh_db_has_owner_columns(
    tmp_path: Path,
) -> None:
    """Fresh DB has owner column on both accounts and sync_state."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        assert "owner" in _account_column_names(connection)
        assert "owner" in _sync_state_column_names(connection)


def test_initialize_database_adds_owner_to_existing_db(tmp_path: Path) -> None:
    """Migration adds owner column to an existing DB that lacks it."""
    db_path = tmp_path / "ledger.db"

    # Bootstrap an old-style DB without owner columns
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                plaid_account_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                mask TEXT,
                type TEXT,
                subtype TEXT,
                institution_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_state (
                id INTEGER PRIMARY KEY,
                item_id TEXT NOT NULL UNIQUE,
                cursor TEXT,
                last_synced_at TEXT
            );
            INSERT INTO accounts
                (plaid_account_id, name, created_at, updated_at)
                VALUES ('acct-old', 'Old Account', '2024-01-01', '2024-01-01');
            INSERT INTO sync_state (item_id, cursor, last_synced_at)
                VALUES ('item-old', 'cur-old', '2024-01-01');
            """
        )

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        assert "owner" in _account_column_names(connection)
        assert "owner" in _sync_state_column_names(connection)
        # Existing data is preserved
        acct = connection.execute(
            "SELECT plaid_account_id, name FROM accounts "
            "WHERE plaid_account_id = 'acct-old'"
        ).fetchone()
        assert acct is not None
        assert acct[0] == "acct-old"


def test_initialize_database_idempotent_with_owner_columns(
    tmp_path: Path,
) -> None:
    """Calling initialize_database twice does not raise an error."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    initialize_database(db_path)  # must not raise

    with sqlite3.connect(db_path) as connection:
        assert "owner" in _account_column_names(connection)
        assert "owner" in _sync_state_column_names(connection)


def test_upsert_account_stores_owner(tmp_path: Path) -> None:
    """upsert_account stores owner and updates it on conflict."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        upsert_account(
            connection, normalize_account_for_db(_account(), owner="alice")
        )
        row = connection.execute(
            "SELECT owner FROM accounts WHERE plaid_account_id = 'acc-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "alice"

        upsert_account(
            connection, normalize_account_for_db(_account(), owner="bob")
        )
        row = connection.execute(
            "SELECT owner FROM accounts WHERE plaid_account_id = 'acc-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "bob"


def test_upsert_account_without_owner_stores_none(tmp_path: Path) -> None:
    """upsert_account with no owner argument stores NULL."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        upsert_account(connection, normalize_account_for_db(_account()))
        row = connection.execute(
            "SELECT owner FROM accounts WHERE plaid_account_id = 'acc-1'"
        ).fetchone()
        assert row is not None
        assert row[0] is None


def test_upsert_sync_state_stores_owner(tmp_path: Path) -> None:
    """upsert_sync_state stores owner and it is readable."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        upsert_sync_state(
            connection,
            item_id="item-1",
            cursor="cur-a",
            owner="shared",
            last_synced_at="2024-01-01T00:00:00+00:00",
        )
        row = connection.execute(
            "SELECT owner FROM sync_state WHERE item_id = 'item-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "shared"


def test_get_all_sync_state_ordered_by_item_id(tmp_path: Path) -> None:
    """get_all_sync_state returns all rows ordered by item_id."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        upsert_sync_state(
            connection,
            item_id="bank-charlie",
            cursor="c3",
            owner="charlie",
            last_synced_at="2024-01-03T00:00:00+00:00",
        )
        upsert_sync_state(
            connection,
            item_id="bank-alice",
            cursor="c1",
            owner="alice",
            last_synced_at="2024-01-01T00:00:00+00:00",
        )
        upsert_sync_state(
            connection,
            item_id="bank-bob",
            cursor="c2",
            owner="bob",
            last_synced_at="2024-01-02T00:00:00+00:00",
        )

        result = get_all_sync_state(connection)

    assert result == [
        SyncStateRow(
            item_id="bank-alice",
            owner="alice",
            last_synced_at="2024-01-01T00:00:00+00:00",
        ),
        SyncStateRow(
            item_id="bank-bob",
            owner="bob",
            last_synced_at="2024-01-02T00:00:00+00:00",
        ),
        SyncStateRow(
            item_id="bank-charlie",
            owner="charlie",
            last_synced_at="2024-01-03T00:00:00+00:00",
        ),
    ]


def test_get_all_sync_state_empty_table(tmp_path: Path) -> None:
    """get_all_sync_state returns empty list when table has no rows."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        assert get_all_sync_state(connection) == []


# ---------------------------------------------------------------------------
# Task 2: canonical_account_id schema, upsert, and apply_account_precedence
# ---------------------------------------------------------------------------


def test_initialize_database_fresh_db_has_canonical_account_id_column(
    tmp_path: Path,
) -> None:
    """Fresh DB has canonical_account_id column on accounts."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("PRAGMA table_info(accounts)").fetchall()
        col_names = {row[1] for row in rows}
        assert "canonical_account_id" in col_names


def test_initialize_database_adds_canonical_account_id_to_existing_db(
    tmp_path: Path,
) -> None:
    """Migration adds canonical_account_id to an existing DB that lacks it."""
    db_path = tmp_path / "ledger.db"

    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                plaid_account_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                mask TEXT,
                type TEXT,
                subtype TEXT,
                institution_name TEXT,
                owner TEXT,
                item_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO accounts
                (plaid_account_id, name, type, created_at, updated_at)
                VALUES ('acct-old', 'Old Account', 'depository',
                        '2024-01-01', '2024-01-01');
            """
        )

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("PRAGMA table_info(accounts)").fetchall()
        col_names = {row[1] for row in rows}
        assert "canonical_account_id" in col_names
        acct = connection.execute(
            "SELECT plaid_account_id FROM accounts "
            "WHERE plaid_account_id = 'acct-old'"
        ).fetchone()
        assert acct is not None


def test_upsert_account_persists_canonical_account_id(tmp_path: Path) -> None:
    """upsert_account stores canonical_account_id when provided."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    row = NormalizedAccountRow(
        plaid_account_id="acct-suppressed",
        name="Suppressed Account",
        mask="9999",
        type="credit",
        subtype=None,
        institution_name=None,
        owner="alice",
        canonical_account_id="acct-canonical",
    )

    with sqlite3.connect(db_path) as connection:
        upsert_account(connection, row)
        stored = connection.execute(
            "SELECT canonical_account_id FROM accounts "
            "WHERE plaid_account_id = 'acct-suppressed'"
        ).fetchone()

    assert stored is not None
    assert stored[0] == "acct-canonical"


def test_upsert_account_canonical_account_id_defaults_to_none(
    tmp_path: Path,
) -> None:
    """upsert_account stores NULL canonical_account_id by default."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        upsert_account(connection, normalize_account_for_db(_account()))
        stored = connection.execute(
            "SELECT canonical_account_id FROM accounts "
            "WHERE plaid_account_id = 'acc-1'"
        ).fetchone()

    assert stored is not None
    assert stored[0] is None


def _make_item(
    item_id: str = "bank-alice",
    suppressed: list[tuple[str, str]] | None = None,
) -> ItemConfig:
    sa_list = tuple(
        SuppressedAccountConfig(plaid_account_id=s, canonical_account_id=c)
        for s, c in (suppressed or [])
    )
    bank_alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    return ItemConfig(
        id=item_id,
        access_token_env=bank_alice_env,
        owner="alice",
        suppressed_accounts=sa_list,
    )


def _insert_bare_account(
    connection: sqlite3.Connection,
    plaid_account_id: str,
) -> None:
    connection.execute(
        "INSERT INTO accounts "
        "(plaid_account_id, name, type, created_at, updated_at) "
        "VALUES (?, 'Test', 'depository', '2024-01-01', '2024-01-01')",
        (plaid_account_id,),
    )


def test_apply_account_precedence_no_aliases_is_noop(tmp_path: Path) -> None:
    """apply_account_precedence with no aliases returns 0."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        _insert_bare_account(connection, "acct-1")
        result = apply_account_precedence(connection, [_make_item()])

    assert result == 0


def test_apply_account_precedence_sets_known_account(tmp_path: Path) -> None:
    """apply_account_precedence sets canonical_account_id for DB accounts."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        _insert_bare_account(connection, "acct-suppressed")
        items = [
            _make_item(suppressed=[("acct-suppressed", "acct-canonical")])
        ]
        result = apply_account_precedence(connection, items)
        stored = connection.execute(
            "SELECT canonical_account_id FROM accounts "
            "WHERE plaid_account_id = 'acct-suppressed'"
        ).fetchone()

    assert result == 1
    assert stored is not None
    assert stored[0] == "acct-canonical"


def test_apply_account_precedence_skips_unknown_account(
    tmp_path: Path,
) -> None:
    """apply_account_precedence skips aliases whose account is not in DB."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        items = [
            _make_item(suppressed=[("acct-not-yet-synced", "acct-canonical")])
        ]
        result = apply_account_precedence(connection, items)

    assert result == 0


def test_apply_account_precedence_clears_stale_suppression(
    tmp_path: Path,
) -> None:
    """apply_account_precedence clears stale canonical_account_id from DB."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        _insert_bare_account(connection, "acct-stale")
        # First run: set suppression
        apply_account_precedence(
            connection,
            [_make_item(suppressed=[("acct-stale", "acct-canonical")])],
        )
        stale_before = connection.execute(
            "SELECT canonical_account_id FROM accounts "
            "WHERE plaid_account_id = 'acct-stale'"
        ).fetchone()

        # Second run: config no longer has this alias
        apply_account_precedence(connection, [_make_item()])
        stale_after = connection.execute(
            "SELECT canonical_account_id FROM accounts "
            "WHERE plaid_account_id = 'acct-stale'"
        ).fetchone()

    assert stale_before is not None
    assert stale_before[0] == "acct-canonical"
    assert stale_after is not None
    assert stale_after[0] is None


def test_apply_account_precedence_idempotent(tmp_path: Path) -> None:
    """Calling apply_account_precedence twice produces the same result."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    items = [_make_item(suppressed=[("acct-suppressed", "acct-canonical")])]

    with sqlite3.connect(db_path) as connection:
        _insert_bare_account(connection, "acct-suppressed")
        apply_account_precedence(connection, items)
        result = apply_account_precedence(connection, items)
        stored = connection.execute(
            "SELECT canonical_account_id FROM accounts "
            "WHERE plaid_account_id = 'acct-suppressed'"
        ).fetchone()

    assert result == 1
    assert stored is not None
    assert stored[0] == "acct-canonical"


_LARGE_HOURS_WINDOW = 24 * 365 * 2
_EXPECTED_ROW_COUNT = 5
_PAGE_SIZE = 2


class TestLedgerErrors:
    """Tests for insert_ledger_error and query_ledger_errors."""

    def _db(self, tmp_path: Path) -> Path:
        """Return path to a freshly initialized test database."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)
        return db_path

    def test_insert_and_query_basic(self, tmp_path: Path) -> None:
        """Insert one WARNING row; query returns it with correct fields."""
        db_path = self._db(tmp_path)
        now = datetime.datetime.now(tz=datetime.UTC)
        row = LedgerErrorRow(
            "WARNING", "test.logger", "something happened", None, now
        )
        with sqlite3.connect(db_path) as conn:
            insert_ledger_error(conn, row)
            rows, total = query_ledger_errors(conn, LedgerErrorQuery())
        assert total == 1
        assert len(rows) == 1
        assert rows[0]["severity"] == "WARNING"
        assert rows[0]["logger_name"] == "test.logger"
        assert rows[0]["message"] == "something happened"

    def test_query_returns_newest_first(self, tmp_path: Path) -> None:
        """Rows with different created_at values are ordered newest first."""
        db_path = self._db(tmp_path)
        base = datetime.datetime.now(tz=datetime.UTC)
        older = base - datetime.timedelta(hours=2)
        newer = base - datetime.timedelta(hours=1)
        with sqlite3.connect(db_path) as conn:
            insert_ledger_error(
                conn, LedgerErrorRow("WARNING", "test", "older", None, older)
            )
            insert_ledger_error(
                conn, LedgerErrorRow("WARNING", "test", "newer", None, newer)
            )
            rows, _ = query_ledger_errors(conn, LedgerErrorQuery())
        assert rows[0]["message"] == "newer"
        assert rows[1]["message"] == "older"

    def test_min_severity_error_filters_warnings(self, tmp_path: Path) -> None:
        """min_severity='ERROR' returns only ERROR rows, not WARNING."""
        db_path = self._db(tmp_path)
        now = datetime.datetime.now(tz=datetime.UTC)
        with sqlite3.connect(db_path) as conn:
            insert_ledger_error(
                conn,
                LedgerErrorRow(
                    "WARNING",
                    "test",
                    "warn msg",
                    None,
                    now - datetime.timedelta(seconds=1),
                ),
            )
            insert_ledger_error(
                conn, LedgerErrorRow("ERROR", "test", "error msg", None, now)
            )
            rows, total = query_ledger_errors(
                conn, LedgerErrorQuery(min_severity="ERROR")
            )
        assert total == 1
        assert rows[0]["severity"] == "ERROR"

    def test_hours_window_excludes_old_rows(self, tmp_path: Path) -> None:
        """A row inserted 48h ago is not returned by a hours=24 query."""
        db_path = self._db(tmp_path)
        old_ts = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(
            hours=48
        )
        with sqlite3.connect(db_path) as conn:
            insert_ledger_error(
                conn,
                LedgerErrorRow("ERROR", "test", "old error", None, old_ts),
            )
            rows, total = query_ledger_errors(conn, LedgerErrorQuery(hours=24))
        assert total == 0
        assert rows == []

    def test_retention_prunes_old_rows(self, tmp_path: Path) -> None:
        """Inserting a future-dated row prunes rows older than 30 days."""
        db_path = self._db(tmp_path)
        now = datetime.datetime.now(tz=datetime.UTC)
        old_ts = now - datetime.timedelta(days=1)
        future_ts = now + datetime.timedelta(days=31)
        with sqlite3.connect(db_path) as conn:
            insert_ledger_error(
                conn,
                LedgerErrorRow("WARNING", "test", "old row", None, old_ts),
            )
            # future_ts is >30 days after old_ts, so old_ts is pruned on insert
            insert_ledger_error(
                conn,
                LedgerErrorRow(
                    "WARNING", "test", "future row", None, future_ts
                ),
            )
            rows, _ = query_ledger_errors(
                conn, LedgerErrorQuery(hours=_LARGE_HOURS_WINDOW)
            )
        messages = [r["message"] for r in rows]
        assert "old row" not in messages
        assert "future row" in messages

    def test_total_reflects_full_count(self, tmp_path: Path) -> None:
        """Total equals full matching count, independent of limit."""
        db_path = self._db(tmp_path)
        now = datetime.datetime.now(tz=datetime.UTC)
        with sqlite3.connect(db_path) as conn:
            for i in range(_EXPECTED_ROW_COUNT):
                insert_ledger_error(
                    conn,
                    LedgerErrorRow(
                        "WARNING",
                        "test",
                        f"msg {i}",
                        None,
                        now - datetime.timedelta(seconds=i),
                    ),
                )
            rows, total = query_ledger_errors(
                conn, LedgerErrorQuery(limit=_PAGE_SIZE, offset=0)
            )
        assert total == _EXPECTED_ROW_COUNT
        assert len(rows) == _PAGE_SIZE

    def test_correlation_id_nullable(self, tmp_path: Path) -> None:
        """A row inserted with correlation_id=None round-trips as None."""
        db_path = self._db(tmp_path)
        now = datetime.datetime.now(tz=datetime.UTC)
        with sqlite3.connect(db_path) as conn:
            insert_ledger_error(
                conn, LedgerErrorRow("WARNING", "test", "no corr", None, now)
            )
            rows, _ = query_ledger_errors(conn, LedgerErrorQuery())
        assert rows[0]["correlation_id"] is None

    def test_empty_table_returns_zero(self, tmp_path: Path) -> None:
        """A fresh DB returns ([], 0) from query_ledger_errors."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            rows, total = query_ledger_errors(conn, LedgerErrorQuery())
        assert rows == []
        assert total == 0


# ---------------------------------------------------------------------------
# AllocationRow and allocation DB helpers
# ---------------------------------------------------------------------------


class TestAllocationRow:
    """AllocationRow construction and default values."""

    def test_required_fields(self) -> None:
        """AllocationRow can be constructed with only required fields."""
        row = AllocationRow(
            plaid_transaction_id="tx-1",
            amount=12.34,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        assert row.plaid_transaction_id == "tx-1"
        assert row.amount == pytest.approx(12.34)
        assert row.id is None
        assert row.category is None
        assert row.tags is None
        assert row.note is None

    def test_optional_fields(self) -> None:
        """AllocationRow stores all optional fields when provided."""
        expected_id = 5
        row = AllocationRow(
            id=expected_id,
            plaid_transaction_id="tx-2",
            amount=50.0,
            category="groceries",
            tags='["food"]',
            note="weekly shop",
            created_at="2024-02-01T00:00:00+00:00",
            updated_at="2024-02-01T00:00:00+00:00",
        )
        assert row.id == expected_id
        assert row.category == "groceries"
        assert row.tags == '["food"]'
        assert row.note == "weekly shop"


class TestUpsertSingleAllocation:
    """upsert_single_allocation insert and update paths."""

    def _db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)
        return db_path

    def _seed_transaction(
        self, connection: sqlite3.Connection, tx_id: str = "tx-1"
    ) -> None:
        connection.execute(
            TRANSACTION_INSERT_SQL,
            (
                tx_id,
                "acct_1",
                25.0,
                "Coffee",
                0,
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
            ),
        )

    def test_insert_path_returns_id(self, tmp_path: Path) -> None:
        """upsert_single_allocation returns a positive integer id on insert."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            row = AllocationRow(
                plaid_transaction_id="tx-1",
                amount=25.0,
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
            alloc_id = upsert_single_allocation(conn, row)
        assert isinstance(alloc_id, int)
        assert alloc_id > 0

    def test_insert_path_stores_fields(self, tmp_path: Path) -> None:
        """Inserted allocation fields are persisted correctly."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            row = AllocationRow(
                plaid_transaction_id="tx-1",
                amount=25.0,
                category="dining",
                tags='["food", "discretionary"]',
                note="lunch",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
            upsert_single_allocation(conn, row)
            allocs = get_allocations_for_transaction(conn, "tx-1")

        assert len(allocs) == 1
        assert allocs[0].amount == pytest.approx(25.0)
        assert allocs[0].category == "dining"
        assert allocs[0].tags == '["food", "discretionary"]'
        assert allocs[0].note == "lunch"

    def test_update_path_updates_existing_row(self, tmp_path: Path) -> None:
        """upsert_single_allocation updates the existing row on second call."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            first = AllocationRow(
                plaid_transaction_id="tx-1",
                amount=25.0,
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
            first_id = upsert_single_allocation(conn, first)

            second = AllocationRow(
                plaid_transaction_id="tx-1",
                amount=25.0,
                category="groceries",
                note="updated note",
                updated_at="2024-01-02T00:00:00+00:00",
                created_at="2024-01-01T00:00:00+00:00",
            )
            second_id = upsert_single_allocation(conn, second)

            allocs = get_allocations_for_transaction(conn, "tx-1")

        assert first_id == second_id
        assert len(allocs) == 1
        assert allocs[0].category == "groceries"
        assert allocs[0].note == "updated note"
        assert allocs[0].updated_at == "2024-01-02T00:00:00+00:00"


class TestGetAllocationsForTransaction:
    """get_allocations_for_transaction return values."""

    def _db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)
        return db_path

    def _seed_transaction(
        self, connection: sqlite3.Connection, tx_id: str = "tx-1"
    ) -> None:
        connection.execute(
            TRANSACTION_INSERT_SQL,
            (
                tx_id,
                "acct_1",
                10.0,
                "Test",
                0,
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
            ),
        )

    def test_empty_when_no_allocations(self, tmp_path: Path) -> None:
        """Returns an empty list when no allocations exist."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            result = get_allocations_for_transaction(conn, "tx-1")
        assert result == []

    def test_returns_one_row(self, tmp_path: Path) -> None:
        """Returns the single allocation row when one exists."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            row = AllocationRow(
                plaid_transaction_id="tx-1",
                amount=10.0,
                category="transport",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
            upsert_single_allocation(conn, row)
            result = get_allocations_for_transaction(conn, "tx-1")

        assert len(result) == 1
        assert result[0].plaid_transaction_id == "tx-1"
        assert result[0].category == "transport"
        assert result[0].id is not None

    def test_ordered_by_id_asc(self, tmp_path: Path) -> None:
        """Multiple rows are returned ordered by id ascending."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            # Insert two rows directly to bypass the 1:1 upsert logic.
            conn.execute(
                "INSERT INTO allocations "
                "(plaid_transaction_id, amount, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    "tx-1",
                    5.0,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO allocations "
                "(plaid_transaction_id, amount, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    "tx-1",
                    5.0,
                    "2024-01-02T00:00:00+00:00",
                    "2024-01-02T00:00:00+00:00",
                ),
            )
            result = get_allocations_for_transaction(conn, "tx-1")

        first, second = result  # unpacking asserts exactly two rows
        assert first.id is not None
        assert second.id is not None
        assert first.id < second.id


class TestUpsertTransactionSeedsAllocation:
    """upsert_transaction creates a blank allocation for new transactions."""

    def _db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)
        return db_path

    def test_creates_allocation_on_insert(self, tmp_path: Path) -> None:
        """New transaction gets a blank allocation row with matching amount."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            upsert_transaction(
                conn,
                _transaction(tx_id="tx-new", amount=42.0),
                now_iso="2024-01-01T00:00:00+00:00",
            )
            allocs = get_allocations_for_transaction(conn, "tx-new")

        assert len(allocs) == 1
        assert allocs[0].amount == pytest.approx(42.0)
        assert allocs[0].category is None
        assert allocs[0].tags is None
        assert allocs[0].note is None

    def test_does_not_duplicate_allocation_on_update(
        self, tmp_path: Path
    ) -> None:
        """Upserting an existing transaction does not add a second row."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            upsert_transaction(
                conn,
                _transaction(tx_id="tx-dup", amount=10.0),
                now_iso="2024-01-01T00:00:00+00:00",
            )
            upsert_transaction(
                conn,
                _transaction(tx_id="tx-dup", amount=10.0),
                now_iso="2024-01-02T00:00:00+00:00",
            )
            allocs = get_allocations_for_transaction(conn, "tx-dup")

        assert len(allocs) == 1


class TestStartupBackfill:
    """Startup backfill populates allocations for existing transactions."""

    def test_backfill_is_idempotent(self, tmp_path: Path) -> None:
        """Running initialize_database twice keeps allocation count stable."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)

        # Seed a transaction directly (bypassing upsert_transaction so no
        # allocation is created by that path).
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO accounts "
                "(plaid_account_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    "acct_bf",
                    "Backfill Acct",
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            )
            conn.execute(
                TRANSACTION_INSERT_SQL,
                (
                    "tx-bf",
                    "acct_bf",
                    99.0,
                    "Backfill",
                    0,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            )
            # Remove the allocation created by initialize_database's backfill
            # so we can test the second call idempotency cleanly.
            conn.execute(
                "DELETE FROM allocations WHERE plaid_transaction_id = 'tx-bf'"
            )

        # First backfill: should create the missing allocation.
        initialize_database(db_path)
        with sqlite3.connect(db_path) as conn:
            count_after_first = conn.execute(
                "SELECT COUNT(*) FROM allocations "
                "WHERE plaid_transaction_id = 'tx-bf'"
            ).fetchone()[0]

        assert count_after_first == 1

        # Second backfill: count must not grow.
        initialize_database(db_path)
        with sqlite3.connect(db_path) as conn:
            count_after_second = conn.execute(
                "SELECT COUNT(*) FROM allocations "
                "WHERE plaid_transaction_id = 'tx-bf'"
            ).fetchone()[0]

        assert count_after_second == 1

    # ------------------------------------------------------------------
    # Helpers shared by annotation-backfill tests
    # ------------------------------------------------------------------

    _ACCT = "acct_ann_bf"
    _NOW = "2024-01-01T00:00:00+00:00"

    def _seed_account(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO accounts "
            "(plaid_account_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (self._ACCT, "Ann Backfill Acct", self._NOW, self._NOW),
        )

    def _seed_transaction(
        self, conn: sqlite3.Connection, tx_id: str, amount: float = 36.85
    ) -> None:
        conn.execute(
            TRANSACTION_INSERT_SQL,
            (tx_id, self._ACCT, amount, "Merchant", 0, self._NOW, self._NOW),
        )

    def _seed_annotation(
        self,
        conn: sqlite3.Connection,
        tx_id: str,
        *,
        category: str | None = "Car",
        note: str | None = "E-470 Express Tolls",
        tags: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO annotations "
            "(plaid_transaction_id, category, note, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tx_id, category, note, tags, self._NOW, self._NOW),
        )

    # ------------------------------------------------------------------
    # INSERT backfill picks up annotation data
    # ------------------------------------------------------------------

    def test_backfill_insert_copies_annotation_data(
        self, tmp_path: Path
    ) -> None:
        """INSERT backfill carries category/note/tags from annotations table."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            self._seed_account(conn)
            self._seed_transaction(conn, "tx-ann-insert")
            self._seed_annotation(conn, "tx-ann-insert")
            # Remove the allocation the first initialize_database call created
            # so we can test the INSERT path cleanly.
            conn.execute(
                "DELETE FROM allocations "
                "WHERE plaid_transaction_id = 'tx-ann-insert'"
            )

        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT category, note, tags FROM allocations "
                "WHERE plaid_transaction_id = 'tx-ann-insert'"
            ).fetchone()

        assert row is not None
        assert row[0] == "Car"
        assert row[1] == "E-470 Express Tolls"
        assert row[2] is None  # no tags seeded

    def test_backfill_insert_no_annotation_leaves_fields_null(
        self, tmp_path: Path
    ) -> None:
        """INSERT backfill without annotation produces null category/note/tags."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            self._seed_account(conn)
            self._seed_transaction(conn, "tx-no-ann")
            conn.execute(
                "DELETE FROM allocations WHERE plaid_transaction_id = 'tx-no-ann'"
            )

        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT category, note, tags FROM allocations "
                "WHERE plaid_transaction_id = 'tx-no-ann'"
            ).fetchone()

        assert row is not None
        assert row[0] is None
        assert row[1] is None
        assert row[2] is None

    # ------------------------------------------------------------------
    # UPDATE migration restores data into existing null stubs
    # ------------------------------------------------------------------

    def test_update_migration_restores_annotation_into_null_stub(
        self, tmp_path: Path
    ) -> None:
        """UPDATE migration fills null-stub allocation from annotations table."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            self._seed_account(conn)
            self._seed_transaction(conn, "tx-restore")
            # Simulate the old bad backfill: allocation row exists but has
            # null category/note/tags even though an annotation is present.
            conn.execute(
                "INSERT OR REPLACE INTO allocations "
                "(plaid_transaction_id, amount, category, note, tags, "
                "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
                ("tx-restore", 36.85, self._NOW, self._NOW),
            )
            self._seed_annotation(conn, "tx-restore")

        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT category, note, tags FROM allocations "
                "WHERE plaid_transaction_id = 'tx-restore'"
            ).fetchone()

        assert row is not None
        assert row[0] == "Car"
        assert row[1] == "E-470 Express Tolls"

    def test_update_migration_does_not_overwrite_existing_allocation_data(
        self, tmp_path: Path
    ) -> None:
        """UPDATE migration skips allocations that already have category data."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            self._seed_account(conn)
            self._seed_transaction(conn, "tx-has-data")
            conn.execute(
                "INSERT OR REPLACE INTO allocations "
                "(plaid_transaction_id, amount, category, note, tags, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                ("tx-has-data", 36.85, "Tolls", "manually set", self._NOW, self._NOW),
            )
            # Annotation exists but has different data
            self._seed_annotation(
                conn, "tx-has-data", category="Car", note="E-470 Express Tolls"
            )

        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT category, note FROM allocations "
                "WHERE plaid_transaction_id = 'tx-has-data'"
            ).fetchone()

        # Must not be overwritten by annotation data
        assert row[0] == "Tolls"
        assert row[1] == "manually set"

    def test_update_migration_does_not_touch_split_allocations(
        self, tmp_path: Path
    ) -> None:
        """UPDATE migration skips transactions with more than one allocation."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            self._seed_account(conn)
            self._seed_transaction(conn, "tx-split", amount=100.0)
            # Two null-stub allocations (simulates a split whose data got wiped)
            for amt in (60.0, 40.0):
                conn.execute(
                    "INSERT INTO allocations "
                    "(plaid_transaction_id, amount, category, note, tags, "
                    "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
                    ("tx-split", amt, self._NOW, self._NOW),
                )
            self._seed_annotation(conn, "tx-split")

        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT category FROM allocations "
                "WHERE plaid_transaction_id = 'tx-split'"
            ).fetchall()

        # Split allocations must not be touched
        assert all(row[0] is None for row in rows)

    def test_update_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Running initialize_database again after restore keeps count stable."""
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            self._seed_account(conn)
            self._seed_transaction(conn, "tx-idem")
            conn.execute(
                "INSERT OR REPLACE INTO allocations "
                "(plaid_transaction_id, amount, category, note, tags, "
                "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
                ("tx-idem", 20.0, self._NOW, self._NOW),
            )
            self._seed_annotation(conn, "tx-idem")

        initialize_database(db_path)
        initialize_database(db_path)

        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM allocations "
                "WHERE plaid_transaction_id = 'tx-idem'"
            ).fetchone()[0]
            row = conn.execute(
                "SELECT category FROM allocations "
                "WHERE plaid_transaction_id = 'tx-idem'"
            ).fetchone()

        assert count == 1
        assert row[0] == "Car"


# ---------------------------------------------------------------------------
# replace_allocations
# ---------------------------------------------------------------------------


class TestReplaceAllocations:
    """replace_allocations insert, replace, and guard paths."""

    _NOW = "2024-01-01T00:00:00+00:00"
    _EXPECTED_INSERT_COUNT = 2
    _EXPECTED_REPLACE_COUNT = 3

    def _db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "ledger.db"
        initialize_database(db_path)
        return db_path

    def _seed_transaction(
        self, connection: sqlite3.Connection, tx_id: str = "tx-r"
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO accounts "
            "(plaid_account_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("acct_r", "Acct R", self._NOW, self._NOW),
        )
        connection.execute(
            TRANSACTION_INSERT_SQL,
            (tx_id, "acct_r", 100.0, "Shop", 0, self._NOW, self._NOW),
        )

    def _row(self, tx_id: str = "tx-r", amount: float = 60.0) -> AllocationRow:
        return AllocationRow(
            plaid_transaction_id=tx_id,
            amount=amount,
            created_at=self._NOW,
            updated_at=self._NOW,
        )

    def test_insert_path_returns_correct_ids(self, tmp_path: Path) -> None:
        """replace_allocations with no prior rows returns positive int IDs."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            rows = [self._row(amount=60.0), self._row(amount=40.0)]
            ids = replace_allocations(conn, "tx-r", rows)

        assert len(ids) == self._EXPECTED_INSERT_COUNT
        assert all(isinstance(i, int) and i > 0 for i in ids)
        assert ids[0] < ids[1]

    def test_replace_path_replaces_and_count_is_new_count(
        self, tmp_path: Path
    ) -> None:
        """replace_allocations replaces prior rows; count matches new set."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            # Seed 2 initial allocations
            replace_allocations(
                conn, "tx-r", [self._row(amount=50.0), self._row(amount=50.0)]
            )
            # Replace with 3 allocations
            new_ids = replace_allocations(
                conn,
                "tx-r",
                [
                    self._row(amount=40.0),
                    self._row(amount=30.0),
                    self._row(amount=30.0),
                ],
            )
            allocs = get_allocations_for_transaction(conn, "tx-r")

        assert len(allocs) == self._EXPECTED_REPLACE_COUNT
        assert len(new_ids) == self._EXPECTED_REPLACE_COUNT
        # Returned IDs match the actual allocation rows in the DB
        db_ids = {a.id for a in allocs}
        assert db_ids == set(new_ids)

    def test_raises_value_error_for_empty_list(self, tmp_path: Path) -> None:
        """replace_allocations raises ValueError when passed an empty list."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            with pytest.raises(ValueError, match="empty"):
                replace_allocations(conn, "tx-r", [])

    def test_does_not_touch_other_transactions(self, tmp_path: Path) -> None:
        """Replacing tx-r allocations leaves tx-other allocations intact."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn, tx_id="tx-r")
            self._seed_transaction(conn, tx_id="tx-other")
            replace_allocations(
                conn, "tx-other", [self._row("tx-other", 99.0)]
            )
            replace_allocations(conn, "tx-r", [self._row("tx-r", 60.0)])
            other_allocs = get_allocations_for_transaction(conn, "tx-other")

        assert len(other_allocs) == 1
        assert other_allocs[0].amount == pytest.approx(99.0)

    def test_fields_are_persisted(self, tmp_path: Path) -> None:
        """replace_allocations stores category, tags, note, and amount."""
        db_path = self._db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            self._seed_transaction(conn)
            row = AllocationRow(
                plaid_transaction_id="tx-r",
                amount=75.5,
                category="groceries",
                tags='["food","weekly"]',
                note="big shop",
                created_at=self._NOW,
                updated_at=self._NOW,
            )
            replace_allocations(conn, "tx-r", [row])
            allocs = get_allocations_for_transaction(conn, "tx-r")

        assert len(allocs) == 1
        a = allocs[0]
        assert a.amount == pytest.approx(75.5)
        assert a.category == "groceries"
        assert a.tags == '["food","weekly"]'
        assert a.note == "big shop"


# ---------------------------------------------------------------------------
# query_transactions — allocation-row pagination
# ---------------------------------------------------------------------------


def test_query_transactions_total_counts_allocation_rows(
    tmp_path: Path,
) -> None:
    """Total reflects allocation rows; a split transaction counts as N rows."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    expected_alloc_count = 2
    now = "2024-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts "
            "(plaid_account_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("acct_s", "Acct S", now, now),
        )
        conn.execute(
            TRANSACTION_INSERT_SQL,
            ("tx-split", "acct_s", 100.0, "Shop", 0, now, now),
        )
        # Insert 2 allocations directly (bypassing the 1:1 upsert).
        conn.execute(
            "INSERT INTO allocations "
            "(plaid_transaction_id, amount, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("tx-split", 60.0, now, now),
        )
        conn.execute(
            "INSERT INTO allocations "
            "(plaid_transaction_id, amount, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("tx-split", 40.0, now, now),
        )

        rows, total = query_transactions(
            conn, TransactionQuery(canonical_only=False)
        )

    assert total == expected_alloc_count
    assert len(rows) == expected_alloc_count
    ids = {r["id"] for r in rows}
    assert ids == {"tx-split"}
