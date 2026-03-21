"""SQLite bootstrap and sync persistence helpers."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claw_plaid_ledger.items_config import ItemConfig
    from claw_plaid_ledger.plaid_models import AccountData, TransactionData

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

logger = logging.getLogger(__name__)


def load_schema_sql() -> str:
    """Load the checked-in SQL schema text."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


def initialize_database(db_path: Path) -> None:
    """Initialize the SQLite database and create schema objects."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.debug("initialize_database db_path=%s", db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(load_schema_sql())
        migration_stmts = (
            "ALTER TABLE accounts ADD COLUMN owner TEXT",
            "ALTER TABLE sync_state ADD COLUMN owner TEXT",
            "ALTER TABLE accounts ADD COLUMN item_id TEXT",
            "ALTER TABLE accounts ADD COLUMN canonical_account_id TEXT",
        )
        for stmt in migration_stmts:
            try:
                connection.execute(stmt)
                logger.info("db migration applied: %s", stmt)
            except sqlite3.OperationalError:
                # Column already exists — schema is current.
                logger.debug("db migration already applied: %s", stmt)
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
    owner: str | None
    item_id: str | None = None
    canonical_account_id: str | None = None


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
    owner: str | None = None,
    item_id: str | None = None,
) -> NormalizedAccountRow:
    """Normalize typed account data for SQLite persistence."""
    return NormalizedAccountRow(
        plaid_account_id=account.plaid_account_id,
        name=account.name,
        mask=account.mask,
        type=account.type,
        subtype=account.subtype,
        institution_name=institution_name,
        owner=owner,
        item_id=item_id,
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
    row: NormalizedAccountRow,
    *,
    now_iso: str | None = None,
) -> None:
    """Insert or update one account row keyed by plaid_account_id."""
    now = now_iso or _utc_now_iso()
    connection.execute(
        (
            "INSERT INTO accounts "
            "(plaid_account_id, name, mask, type, subtype, "
            "institution_name, owner, item_id, canonical_account_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(plaid_account_id) DO UPDATE SET "
            "name = excluded.name, "
            "mask = excluded.mask, "
            "type = excluded.type, "
            "subtype = excluded.subtype, "
            "institution_name = excluded.institution_name, "
            "owner = excluded.owner, "
            "item_id = excluded.item_id, "
            "canonical_account_id = excluded.canonical_account_id, "
            "updated_at = excluded.updated_at"
        ),
        (
            row.plaid_account_id,
            row.name,
            row.mask,
            row.type,
            row.subtype,
            row.institution_name,
            row.owner,
            row.item_id,
            row.canonical_account_id,
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


def apply_account_precedence(
    connection: sqlite3.Connection,
    items: list[ItemConfig],
) -> int:
    """
    Write canonical_account_id to suppressed accounts.

    For each SuppressedAccountConfig across all items, sets
    accounts.canonical_account_id = canonical_account_id WHERE
    plaid_account_id = suppressed plaid_account_id.

    Also clears canonical_account_id to NULL for any account not currently
    listed as suppressed in the config (config is the single source of truth).

    Returns the count of rows updated (set to a non-null canonical_account_id).
    """
    suppressed_map: dict[str, str] = {}
    for item in items:
        for sa in item.suppressed_accounts:
            suppressed_map[sa.plaid_account_id] = sa.canonical_account_id

    # Clear all existing suppressions first; we will re-apply from config.
    # This handles stale suppressions (accounts removed from config).
    connection.execute(
        "UPDATE accounts SET canonical_account_id = NULL "
        "WHERE canonical_account_id IS NOT NULL"
    )

    updated = 0
    for plaid_account_id, canonical_account_id in suppressed_map.items():
        cursor = connection.execute(
            "UPDATE accounts SET canonical_account_id = ? "
            "WHERE plaid_account_id = ?",
            (canonical_account_id, plaid_account_id),
        )
        updated += cursor.rowcount

    return updated


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
    canonical_only: bool = True
    limit: int = 100
    offset: int = 0
    tags: tuple[str, ...] = ()
    search_notes: bool = False


def _apply_tag_filters(
    tags: tuple[str, ...],
    where_parts: list[str],
    params: list[object],
) -> None:
    """
    Append an EXISTS clause and param for each required tag (AND semantics).

    An unannotated transaction (ann.tags IS NULL) never matches because
    json_each(NULL) returns zero rows.
    """
    for tag in tags:
        where_parts.append(
            "EXISTS (SELECT 1 FROM json_each(ann.tags) WHERE value = ?)"
        )
        params.append(tag)


def query_transactions(
    connection: sqlite3.Connection,
    query: TransactionQuery,
) -> tuple[list[dict[str, object]], int]:
    """Return filtered transaction rows and the full matching count."""
    effective_date_sql = "COALESCE(t.posted_date, t.authorized_date)"

    accounts_join = (
        "JOIN accounts a ON a.plaid_account_id = t.plaid_account_id "
        if query.canonical_only
        else ""
    )
    # Always LEFT JOIN annotations so transactions without annotations are
    # still returned when no tag filter or search_notes is active.
    annotations_join = (
        "LEFT JOIN annotations ann "
        "ON ann.plaid_transaction_id = t.plaid_transaction_id "
    )

    where_parts: list[str] = []
    params: list[object] = []

    if query.start_date is not None:
        where_parts.append(f"{effective_date_sql} >= ?")
        params.append(query.start_date)

    if query.end_date is not None:
        where_parts.append(f"{effective_date_sql} <= ?")
        params.append(query.end_date)

    if query.account_id is not None:
        where_parts.append("t.plaid_account_id = ?")
        params.append(query.account_id)

    if query.pending is not None:
        where_parts.append("t.pending = ?")
        params.append(int(query.pending))

    if query.min_amount is not None:
        where_parts.append("t.amount >= ?")
        params.append(query.min_amount)

    if query.max_amount is not None:
        where_parts.append("t.amount <= ?")
        params.append(query.max_amount)

    if query.keyword is not None:
        keyword_like = f"%{query.keyword}%"
        if query.search_notes:
            where_parts.append(
                "(t.name LIKE ? OR t.merchant_name LIKE ? OR ann.note LIKE ?)"
            )
            params.extend([keyword_like, keyword_like, keyword_like])
        else:
            where_parts.append("(t.name LIKE ? OR t.merchant_name LIKE ?)")
            params.extend([keyword_like, keyword_like])

    if query.canonical_only:
        where_parts.append("a.canonical_account_id IS NULL")

    _apply_tag_filters(query.tags, where_parts, params)

    where_sql = " AND ".join(where_parts) if where_parts else "1=1"
    from_clause = f"FROM transactions t {accounts_join}{annotations_join}"

    total_row = connection.execute(
        f"SELECT COUNT(*) {from_clause}WHERE {where_sql}",
        params,
    ).fetchone()
    total = int(total_row[0]) if total_row is not None else 0

    rows = connection.execute(
        (
            f"SELECT t.plaid_transaction_id, t.plaid_account_id, t.amount, "
            "t.iso_currency_code, t.name, t.merchant_name, t.pending, "
            f"{effective_date_sql} AS effective_date "
            f"{from_clause}"
            f"WHERE {where_sql} "
            "ORDER BY effective_date DESC, t.plaid_transaction_id ASC "
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
            "SELECT t.plaid_transaction_id, t.plaid_account_id, t.amount, "
            "t.iso_currency_code, t.name, t.merchant_name, t.pending, "
            "COALESCE(posted_date, authorized_date) AS effective_date, "
            "raw_json, a.canonical_account_id "
            "FROM transactions t "
            "LEFT JOIN accounts a ON a.plaid_account_id = t.plaid_account_id "
            "WHERE t.plaid_transaction_id = ?"
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
        "suppressed_by": str(row[9]) if row[9] is not None else None,
    }


@dataclass(frozen=True)
class SpendQuery:
    """Filters for the aggregate spend query."""

    start_date: str
    end_date: str
    owner: str | None = None
    tags: tuple[str, ...] = ()
    include_pending: bool = False
    canonical_only: bool = True


def query_spend(
    connection: sqlite3.Connection,
    query: SpendQuery,
) -> tuple[float, int]:
    """Return (total_spend, transaction_count) for matching transactions."""
    effective_date_sql = "COALESCE(t.posted_date, t.authorized_date)"

    need_accounts_join = query.canonical_only or query.owner is not None
    accounts_join = (
        "JOIN accounts a ON a.plaid_account_id = t.plaid_account_id "
        if need_accounts_join
        else ""
    )
    annotations_join = (
        "LEFT JOIN annotations ann "
        "ON ann.plaid_transaction_id = t.plaid_transaction_id "
    )

    where_parts: list[str] = [
        f"{effective_date_sql} >= ?",
        f"{effective_date_sql} <= ?",
    ]
    params: list[object] = [query.start_date, query.end_date]

    if query.canonical_only:
        where_parts.append("a.canonical_account_id IS NULL")

    if not query.include_pending:
        where_parts.append("t.pending = 0")

    if query.owner is not None:
        where_parts.append("a.owner = ?")
        params.append(query.owner)

    for tag in query.tags:
        where_parts.append(
            "EXISTS (SELECT 1 FROM json_each(ann.tags) WHERE value = ?)"
        )
        params.append(tag)

    where_sql = " AND ".join(where_parts)
    # S608: accounts_join, annotations_join, and where_sql are built from
    # hard-coded SQL strings only.  Every user-supplied value (dates, owner,
    # tag strings) is bound via the params list as a `?` placeholder, so
    # there is no injection risk.  Ruff cannot prove the fragments are safe
    # from static analysis alone, making the noqa unavoidable here.
    row = connection.execute(
        (
            "SELECT COALESCE(SUM(t.amount), 0.0), COUNT(*) "  # noqa: S608
            "FROM transactions t "
            f"{accounts_join}"
            f"{annotations_join}"
            f"WHERE {where_sql}"
        ),
        params,
    ).fetchone()
    if row is None:
        return 0.0, 0
    total_spend = float(row[0]) if row[0] is not None else 0.0
    count = int(row[1]) if row[1] is not None else 0
    return total_spend, count


def get_distinct_categories(connection: sqlite3.Connection) -> list[str]:
    """Return distinct non-null category values sorted alphabetically."""
    rows = connection.execute(
        "SELECT DISTINCT category FROM annotations "
        "WHERE category IS NOT NULL "
        "ORDER BY category COLLATE NOCASE"
    ).fetchall()
    return [str(row[0]) for row in rows]


def get_distinct_tags(connection: sqlite3.Connection) -> list[str]:
    """
    Return distinct tag values unnested from all annotation rows.

    Results are sorted alphabetically (case-insensitive).
    """
    rows = connection.execute(
        "SELECT DISTINCT j.value "
        "FROM annotations a, json_each(a.tags) j "
        "WHERE a.tags IS NOT NULL "
        "ORDER BY j.value COLLATE NOCASE"
    ).fetchall()
    return [str(row[0]) for row in rows]


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
    owner: str | None = None,
    last_synced_at: str | None = None,
) -> None:
    """Insert or update sync cursor state for a Plaid item."""
    synced_at = last_synced_at or _utc_now_iso()
    connection.execute(
        (
            "INSERT INTO sync_state (item_id, cursor, owner, last_synced_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(item_id) DO UPDATE SET "
            "cursor = excluded.cursor, "
            "owner = excluded.owner, "
            "last_synced_at = excluded.last_synced_at"
        ),
        (item_id, cursor, owner, synced_at),
    )


@dataclass(frozen=True)
class SyncStateRow:
    """One row from sync_state with owner and last-synced timestamp."""

    item_id: str
    owner: str | None
    last_synced_at: str | None


def get_all_sync_state(
    connection: sqlite3.Connection,
) -> list[SyncStateRow]:
    """Return all sync_state rows ordered by item_id."""
    rows = connection.execute(
        "SELECT item_id, owner, last_synced_at "
        "FROM sync_state ORDER BY item_id"
    ).fetchall()
    return [
        SyncStateRow(
            item_id=str(row[0]),
            owner=str(row[1]) if row[1] is not None else None,
            last_synced_at=str(row[2]) if row[2] is not None else None,
        )
        for row in rows
    ]
