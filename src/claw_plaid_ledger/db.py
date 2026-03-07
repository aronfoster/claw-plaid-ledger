"""SQLite bootstrap helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def load_schema_sql() -> str:
    """Load the checked-in SQL schema text."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


def initialize_database(db_path: Path) -> None:
    """Initialize the SQLite database and create schema objects."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        connection.executescript(load_schema_sql())
        connection.commit()
