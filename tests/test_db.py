"""Database bootstrap tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from claw_plaid_ledger.db import initialize_database

if TYPE_CHECKING:
    from pathlib import Path


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
    assert {"accounts", "transactions", "sync_state"}.issubset(table_names)


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    """Initialization can run repeatedly without errors."""
    db_path = tmp_path / "ledger.db"

    initialize_database(db_path)
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        indexes = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()

    index_names = {name for (name,) in indexes if name}
    assert "sqlite_autoindex_accounts_1" in index_names
    assert "sqlite_autoindex_transactions_1" in index_names
