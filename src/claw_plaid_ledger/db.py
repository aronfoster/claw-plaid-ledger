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


def delete_transaction(
    connection: sqlite3.Connection,
    *,
    plaid_transaction_id: str,
) -> None:
    """Delete one transaction by Plaid transaction id if it exists."""
    connection.execute(
        "DELETE FROM transactions WHERE plaid_transaction_id = ?",
        (plaid_transaction_id,),
    )


@dataclass(frozen=True)
class AnnotationRow:
    """Normalized annotation fields ready for SQL parameter binding."""

    plaid_transaction_id: str
    category: str | None
    note: str | None
    tags: str | None
    created_at: str
    updated_at: str


def upsert_annotation(
    connection: sqlite3.Connection,
    row: AnnotationRow,
) -> None:
    """Insert or update one annotation keyed by plaid_transaction_id."""
    connection.execute(
        (
            "INSERT INTO annotations "
            "(plaid_transaction_id, category, note, tags, created_at, "
            "updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(plaid_transaction_id) DO UPDATE SET "
            "category = excluded.category, "
            "note = excluded.note, "
            "tags = excluded.tags, "
            "updated_at = excluded.updated_at"
        ),
        (
            row.plaid_transaction_id,
            row.category,
            row.note,
            row.tags,
            row.created_at,
            row.updated_at,
        ),
    )


def get_annotation(
    connection: sqlite3.Connection,
    plaid_transaction_id: str,
) -> AnnotationRow | None:
    """Return one annotation by Plaid transaction id, if present."""
    db_row = connection.execute(
        (
            "SELECT plaid_transaction_id, category, note, tags, created_at, "
            "updated_at FROM annotations WHERE plaid_transaction_id = ?"
        ),
        (plaid_transaction_id,),
    ).fetchone()
    if db_row is None:
        return None
    return AnnotationRow(
        plaid_transaction_id=str(db_row[0]),
        category=str(db_row[1]) if db_row[1] is not None else None,
        note=str(db_row[2]) if db_row[2] is not None else None,
        tags=str(db_row[3]) if db_row[3] is not None else None,
        created_at=str(db_row[4]),
        updated_at=str(db_row[5]),
    )


@dataclass(frozen=True)
class TransactionQuery:
    """Transaction list filters and pagination controls for DB queries."""

    start_date: str | None = None
    end_date: str | None = None
    account_id: str | None = None
    pending: bool | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    keyword: str | None = None
    limit: int = 100
    offset: int = 0


def query_transactions(
    connection: sqlite3.Connection,
    query: TransactionQuery,
) -> tuple[list[dict[str, object]], int]:
    """Return filtered transaction rows and the full matching count."""
    pending_value = int(query.pending) if query.pending is not None else None
    keyword_like = f"%{query.keyword}%" if query.keyword is not None else None

    params: tuple[object, ...] = (
        query.start_date,
        query.start_date,
        query.end_date,
        query.end_date,
        query.account_id,
        query.account_id,
        pending_value,
        pending_value,
        query.min_amount,
        query.min_amount,
        query.max_amount,
        query.max_amount,
        keyword_like,
        keyword_like,
        keyword_like,
    )

    total_row = connection.execute(
        (
            "SELECT COUNT(*) FROM transactions "
            "WHERE (? IS NULL OR COALESCE(posted_date, authorized_date) >= ?) "
            "AND (? IS NULL OR COALESCE(posted_date, authorized_date) <= ?) "
            "AND (? IS NULL OR plaid_account_id = ?) "
            "AND (? IS NULL OR pending = ?) "
            "AND (? IS NULL OR amount >= ?) "
            "AND (? IS NULL OR amount <= ?) "
            "AND (? IS NULL OR (name LIKE ? OR merchant_name LIKE ?))"
        ),
        params,
    ).fetchone()
    total = int(total_row[0]) if total_row is not None else 0

    rows = connection.execute(
        (
            "SELECT plaid_transaction_id, plaid_account_id, amount, "
            "iso_currency_code, name, merchant_name, pending, "
            "COALESCE(posted_date, authorized_date) AS effective_date "
            "FROM transactions "
            "WHERE (? IS NULL OR COALESCE(posted_date, authorized_date) >= ?) "
            "AND (? IS NULL OR COALESCE(posted_date, authorized_date) <= ?) "
            "AND (? IS NULL OR plaid_account_id = ?) "
            "AND (? IS NULL OR pending = ?) "
            "AND (? IS NULL OR amount >= ?) "
            "AND (? IS NULL OR amount <= ?) "
            "AND (? IS NULL OR (name LIKE ? OR merchant_name LIKE ?)) "
            "ORDER BY effective_date DESC, plaid_transaction_id ASC "
            "LIMIT ? OFFSET ?"
        ),
        (*params, query.limit, query.offset),
    ).fetchall()

    parsed_rows: list[dict[str, object]] = [
        {
            "id": str(row[0]),
            "account_id": str(row[1]),
            "amount": float(row[2]),
            "iso_currency_code": str(row[3]) if row[3] is not None else None,
            "name": str(row[4]),
            "merchant_name": str(row[5]) if row[5] is not None else None,
            "pending": bool(row[6]),
            "date": str(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]

    return parsed_rows, total


def get_transaction(
    connection: sqlite3.Connection,
    plaid_transaction_id: str,
) -> dict[str, object] | None:
    """Return one transaction by Plaid transaction id, if present."""
    row = connection.execute(
        (
            "SELECT plaid_transaction_id, plaid_account_id, amount, "
            "iso_currency_code, name, merchant_name, pending, "
            "COALESCE(posted_date, authorized_date) AS effective_date, "
            "raw_json "
            "FROM transactions WHERE plaid_transaction_id = ?"
        ),
        (plaid_transaction_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "account_id": str(row[1]),
        "amount": float(row[2]),
        "iso_currency_code": str(row[3]) if row[3] is not None else None,
        "name": str(row[4]),
        "merchant_name": str(row[5]) if row[5] is not None else None,
        "pending": bool(row[6]),
        "date": str(row[7]) if row[7] is not None else None,
        "raw_json": str(row[8]) if row[8] is not None else None,
    }


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
