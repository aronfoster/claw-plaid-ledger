"""SQLite bootstrap and sync persistence helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claw_plaid_ledger.plaid_models import AccountData, TransactionData

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


@dataclass(frozen=True)
class NormalizedAccountRow:
    """Normalized account fields ready for SQL parameter binding."""

    plaid_account_id: str
    name: str
    mask: str | None
    type: str
    subtype: str | None
    institution_name: str | None


@dataclass(frozen=True)
class NormalizedTransactionRow:
    """Normalized transaction fields ready for SQL parameter binding."""

    plaid_transaction_id: str
    plaid_account_id: str
    amount: float
    iso_currency_code: str | None
    name: str
    merchant_name: str | None
    pending: int
    authorized_date: str | None
    posted_date: str | None
    raw_json: str | None


def normalize_account_for_db(
    account: AccountData,
    *,
    institution_name: str | None = None,
) -> NormalizedAccountRow:
    """Normalize typed account data for SQLite persistence."""
    return NormalizedAccountRow(
        plaid_account_id=account.plaid_account_id,
        name=account.name,
        mask=account.mask,
        type=account.type,
        subtype=account.subtype,
        institution_name=institution_name,
    )


def normalize_transaction_for_db(
    transaction: TransactionData,
) -> NormalizedTransactionRow:
    """Normalize typed transaction data for SQLite persistence."""
    date_text = transaction.date.isoformat()
    authorized_date = date_text if transaction.pending else None
    posted_date = None if transaction.pending else date_text
    return NormalizedTransactionRow(
        plaid_transaction_id=transaction.plaid_transaction_id,
        plaid_account_id=transaction.plaid_account_id,
        amount=transaction.amount,
        iso_currency_code=transaction.iso_currency_code,
        name=transaction.name,
        merchant_name=transaction.merchant_name,
        pending=int(transaction.pending),
        authorized_date=authorized_date,
        posted_date=posted_date,
        raw_json=None,
    )


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def upsert_account(
    connection: sqlite3.Connection,
    account: AccountData,
    *,
    institution_name: str | None = None,
    now_iso: str | None = None,
) -> None:
    """Insert or update one account keyed by plaid_account_id."""
    now = now_iso or _utc_now_iso()
    row = normalize_account_for_db(account, institution_name=institution_name)
    connection.execute(
        (
            "INSERT INTO accounts "
            "(plaid_account_id, name, mask, type, subtype, institution_name, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(plaid_account_id) DO UPDATE SET "
            "name = excluded.name, "
            "mask = excluded.mask, "
            "type = excluded.type, "
            "subtype = excluded.subtype, "
            "institution_name = excluded.institution_name, "
            "updated_at = excluded.updated_at"
        ),
        (
            row.plaid_account_id,
            row.name,
            row.mask,
            row.type,
            row.subtype,
            row.institution_name,
            now,
            now,
        ),
    )


def upsert_transaction(
    connection: sqlite3.Connection,
    transaction: TransactionData,
    *,
    now_iso: str | None = None,
) -> None:
    """Insert or update one transaction keyed by plaid_transaction_id."""
    now = now_iso or _utc_now_iso()
    row = normalize_transaction_for_db(transaction)
    connection.execute(
        (
            "INSERT INTO transactions "
            "(plaid_transaction_id, plaid_account_id, amount, "
            "iso_currency_code, "
            "name, merchant_name, pending, authorized_date, posted_date, "
            "raw_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(plaid_transaction_id) DO UPDATE SET "
            "plaid_account_id = excluded.plaid_account_id, "
            "amount = excluded.amount, "
            "iso_currency_code = excluded.iso_currency_code, "
            "name = excluded.name, "
            "merchant_name = excluded.merchant_name, "
            "pending = excluded.pending, "
            "authorized_date = excluded.authorized_date, "
            "posted_date = excluded.posted_date, "
            "raw_json = excluded.raw_json, "
            "updated_at = excluded.updated_at"
        ),
        (
            row.plaid_transaction_id,
            row.plaid_account_id,
            row.amount,
            row.iso_currency_code,
            row.name,
            row.merchant_name,
            row.pending,
            row.authorized_date,
            row.posted_date,
            row.raw_json,
            now,
            now,
        ),
    )


def get_sync_cursor(
    connection: sqlite3.Connection, item_id: str
) -> str | None:
    """Return the persisted sync cursor for an item, if present."""
    row = connection.execute(
        "SELECT cursor FROM sync_state WHERE item_id = ?", (item_id,)
    ).fetchone()
    if row is None:
        return None
    return str(row[0]) if row[0] is not None else None


def upsert_sync_state(
    connection: sqlite3.Connection,
    *,
    item_id: str,
    cursor: str | None,
    last_synced_at: str | None = None,
) -> None:
    """Insert or update sync cursor state for a Plaid item."""
    synced_at = last_synced_at or _utc_now_iso()
    connection.execute(
        (
            "INSERT INTO sync_state (item_id, cursor, last_synced_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(item_id) DO UPDATE SET "
            "cursor = excluded.cursor, "
            "last_synced_at = excluded.last_synced_at"
        ),
        (item_id, cursor, synced_at),
    )
