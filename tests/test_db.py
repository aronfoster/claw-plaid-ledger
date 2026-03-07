"""Database bootstrap and schema constraint tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.db import initialize_database

TRANSACTION_INSERT_SQL = (
    "INSERT INTO transactions "
    "(plaid_transaction_id, plaid_account_id, amount, name, pending, "
    "created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

if TYPE_CHECKING:
    from pathlib import Path


REQUIRED_TABLES = {"accounts", "transactions", "sync_state"}


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
