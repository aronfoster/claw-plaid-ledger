"""SQLite bootstrap and sync persistence helpers."""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        connection.execute("DROP TABLE IF EXISTS annotations")
        # Migration: add plaid_item_id to sync_state for existing databases
        # that were created before this column existed.
        with contextlib.suppress(sqlite3.OperationalError):
            connection.execute(
                "ALTER TABLE sync_state ADD COLUMN plaid_item_id TEXT"
            )
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
    # Seed a blank allocation for new transactions without one. Because
    # allocations has no UNIQUE constraint on plaid_transaction_id, raw
    # INSERT OR IGNORE cannot be used; instead we guard with NOT EXISTS.
    connection.execute(
        "INSERT INTO allocations "
        "(plaid_transaction_id, amount, created_at, updated_at) "
        "SELECT ?, ?, ?, ? "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM allocations WHERE plaid_transaction_id = ?"
        ")",
        (
            row.plaid_transaction_id,
            row.amount,
            now,
            now,
            row.plaid_transaction_id,
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


@dataclass
class AllocationRow:
    """One allocation row from the allocations table."""

    plaid_transaction_id: str
    amount: float
    created_at: str
    updated_at: str
    id: int | None = None
    category: str | None = None
    tags: str | None = None
    note: str | None = None


def upsert_single_allocation(
    connection: sqlite3.Connection,
    row: AllocationRow,
) -> int:
    """
    Insert or update the single allocation for a transaction.

    Updates the first/only allocation row for *plaid_transaction_id* if one
    exists; otherwise inserts a new row. Returns the allocation id.
    """
    existing = connection.execute(
        "SELECT id FROM allocations WHERE plaid_transaction_id = ? "
        "ORDER BY id ASC LIMIT 1",
        (row.plaid_transaction_id,),
    ).fetchone()

    if existing is not None:
        alloc_id = int(existing[0])
        connection.execute(
            "UPDATE allocations SET amount = ?, category = ?, tags = ?, "
            "note = ?, updated_at = ? WHERE id = ?",
            (
                row.amount,
                row.category,
                row.tags,
                row.note,
                row.updated_at,
                alloc_id,
            ),
        )
        return alloc_id

    cursor = connection.execute(
        "INSERT INTO allocations "
        "(plaid_transaction_id, amount, category, tags, note, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            row.plaid_transaction_id,
            row.amount,
            row.category,
            row.tags,
            row.note,
            row.created_at,
            row.updated_at,
        ),
    )
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def get_allocations_for_transaction(
    connection: sqlite3.Connection,
    plaid_transaction_id: str,
) -> list[AllocationRow]:
    """Return all allocation rows for a transaction, ordered by id ASC."""
    rows = connection.execute(
        "SELECT id, plaid_transaction_id, amount, category, tags, note, "
        "created_at, updated_at FROM allocations "
        "WHERE plaid_transaction_id = ? ORDER BY id ASC",
        (plaid_transaction_id,),
    ).fetchall()
    return [
        AllocationRow(
            id=int(row[0]),
            plaid_transaction_id=str(row[1]),
            amount=float(row[2]),
            category=str(row[3]) if row[3] is not None else None,
            tags=str(row[4]) if row[4] is not None else None,
            note=str(row[5]) if row[5] is not None else None,
            created_at=str(row[6]),
            updated_at=str(row[7]),
        )
        for row in rows
    ]


def replace_allocations(
    connection: sqlite3.Connection,
    plaid_transaction_id: str,
    rows: list[AllocationRow],
) -> list[int]:
    """
    Atomically replace all allocations for a transaction.

    Raises ValueError if rows is empty (guard against accidental data loss).
    Deletes all existing allocations for plaid_transaction_id and inserts
    each row in order. Returns the list of inserted id values.
    """
    if not rows:
        msg = "rows must not be empty; at least one allocation is required"
        raise ValueError(msg)
    connection.execute(
        "DELETE FROM allocations WHERE plaid_transaction_id = ?",
        (plaid_transaction_id,),
    )
    ids: list[int] = []
    for row in rows:
        cursor = connection.execute(
            "INSERT INTO allocations "
            "(plaid_transaction_id, amount, category, tags, note, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row.plaid_transaction_id,
                row.amount,
                row.category,
                row.tags,
                row.note,
                row.created_at,
                row.updated_at,
            ),
        )
        row_id = cursor.lastrowid
        if row_id is None:
            msg = "INSERT INTO allocations produced no lastrowid"
            raise RuntimeError(msg)
        ids.append(row_id)
    return ids


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
    categories: tuple[str, ...] = ()
    search_notes: bool = False
    uncategorized_only: bool = False
    splits_only: bool = False


def _apply_tag_filters(
    tags: tuple[str, ...],
    where_parts: list[str],
    params: list[object],
) -> None:
    """
    Append an EXISTS clause and param for each required tag (AND semantics).

    A transaction with no allocation or null alloc.tags never matches because
    json_each(NULL) returns zero rows.
    """
    for tag in tags:
        where_parts.append(
            "EXISTS (SELECT 1 FROM json_each(alloc.tags) WHERE value = ?)"
        )
        params.append(tag)


def _apply_uncategorized_filter(
    *,
    uncategorized_only: bool,
    where_parts: list[str],
) -> None:
    """Append uncategorized-only predicate when requested."""
    if uncategorized_only:
        where_parts.append("alloc.category IS NULL")


def _apply_splits_filter(
    *,
    splits_only: bool,
    where_parts: list[str],
) -> None:
    """Append split-transaction-only predicate when requested."""
    if splits_only:
        where_parts.append(
            "t.plaid_transaction_id IN ("
            "SELECT plaid_transaction_id FROM allocations "
            "GROUP BY plaid_transaction_id HAVING COUNT(*) > 1"
            ")"
        )


def _apply_category_filters(
    categories: tuple[str, ...],
    where_parts: list[str],
    params: list[object],
) -> None:
    """
    Append a single OR-joined category predicate (case-insensitive).

    Matches per allocation row against ``alloc.category``. NULL categories
    never match a named-category filter. An empty tuple is a no-op.
    """
    if not categories:
        return
    or_parts = " OR ".join(
        ["LOWER(alloc.category) = LOWER(?)"] * len(categories)
    )
    where_parts.append("(" + or_parts + ")")
    params.extend(categories)


def _allocation_from_joined_row(
    cols: tuple[Any, ...],
) -> dict[str, object] | None:
    """
    Build an allocation payload from LEFT-JOIN columns, or return None.

    *cols* must contain six elements in this order:
    alloc.id, alloc.amount, alloc.category, alloc.note,
    alloc.tags (JSON string), alloc.updated_at.
    """
    alloc_id, amount, category, note, tags_json, updated_at = cols
    if alloc_id is None:
        return None
    alloc_tags: list[str] | None = None
    if tags_json is not None:
        alloc_tags = [str(t) for t in json.loads(str(tags_json))]
    return {
        "id": int(alloc_id),
        "amount": float(amount) if amount is not None else None,
        "category": str(category) if category is not None else None,
        "note": str(note) if note is not None else None,
        "tags": alloc_tags,
        "updated_at": str(updated_at) if updated_at is not None else None,
    }


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
    # Always LEFT JOIN allocations so transactions without allocations are
    # still returned when no tag filter or search_notes is active.
    allocations_join = (
        "LEFT JOIN allocations alloc "
        "ON alloc.plaid_transaction_id = t.plaid_transaction_id "
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
                "(t.name LIKE ? OR t.merchant_name LIKE ?"
                " OR alloc.note LIKE ?)"
            )
            params.extend([keyword_like, keyword_like, keyword_like])
        else:
            where_parts.append("(t.name LIKE ? OR t.merchant_name LIKE ?)")
            params.extend([keyword_like, keyword_like])

    if query.canonical_only:
        where_parts.append("a.canonical_account_id IS NULL")

    _apply_tag_filters(query.tags, where_parts, params)
    _apply_category_filters(query.categories, where_parts, params)
    _apply_uncategorized_filter(
        uncategorized_only=query.uncategorized_only,
        where_parts=where_parts,
    )
    _apply_splits_filter(
        splits_only=query.splits_only,
        where_parts=where_parts,
    )

    where_sql = " AND ".join(where_parts) if where_parts else "1=1"
    from_clause = f"FROM transactions t {accounts_join}{allocations_join}"

    total_row = connection.execute(
        f"SELECT COUNT(*) {from_clause}WHERE {where_sql}",
        params,
    ).fetchone()
    total = int(total_row[0]) if total_row is not None else 0

    rows = connection.execute(
        (
            f"SELECT t.plaid_transaction_id, t.plaid_account_id, t.amount, "
            "t.iso_currency_code, t.name, t.merchant_name, t.pending, "
            f"{effective_date_sql} AS effective_date, "
            "alloc.id, alloc.amount, alloc.category, alloc.note, "
            "alloc.tags, alloc.updated_at "
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
            "allocation": _allocation_from_joined_row(row[8:14]),
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
    account_id: str | None = None
    categories: tuple[str, ...] = ()
    tag: str | None = None


def query_spend(
    connection: sqlite3.Connection,
    query: SpendQuery,
) -> tuple[float, int]:
    """Return (total_spend, allocation_count) for matching allocations."""
    effective_date_sql = "COALESCE(t.posted_date, t.authorized_date)"

    need_accounts_join = query.canonical_only or query.owner is not None
    accounts_join = (
        "JOIN accounts a ON a.plaid_account_id = t.plaid_account_id "
        if need_accounts_join
        else ""
    )
    allocations_join = (
        "LEFT JOIN allocations alloc "
        "ON alloc.plaid_transaction_id = t.plaid_transaction_id "
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
            "EXISTS (SELECT 1 FROM json_each(alloc.tags) WHERE value = ?)"
        )
        params.append(tag)

    if query.account_id is not None:
        where_parts.append("t.plaid_account_id = ?")
        params.append(query.account_id)

    _apply_category_filters(query.categories, where_parts, params)

    if query.tag is not None:
        where_parts.append(
            "EXISTS ("
            "SELECT 1 FROM json_each(alloc.tags) WHERE LOWER(value) = LOWER(?)"
            ")"
        )
        params.append(query.tag)

    where_sql = " AND ".join(where_parts)
    # S608: accounts_join, allocations_join, and where_sql are built from
    # hard-coded SQL strings only.  Every user-supplied value (dates, owner,
    # tag strings) is bound via the params list as a `?` placeholder, so
    # there is no injection risk.  Ruff cannot prove the fragments are safe
    # from static analysis alone, making the noqa unavoidable here.
    # The same rationale applies to query_spend_trends() below.
    row = connection.execute(
        (
            "SELECT COALESCE(SUM(alloc.amount), 0.0), COUNT(*) "  # noqa: S608
            "FROM transactions t "
            f"{accounts_join}"
            f"{allocations_join}"
            f"WHERE {where_sql}"
        ),
        params,
    ).fetchone()
    if row is None:
        return 0.0, 0
    total_spend = float(row[0]) if row[0] is not None else 0.0
    count = int(row[1]) if row[1] is not None else 0
    return total_spend, count


@dataclass(frozen=True)
class SpendTrendsQuery:
    """Filters for the monthly spend trends query."""

    months: int
    owner: str | None = None
    tags: tuple[str, ...] = ()
    include_pending: bool = False
    canonical_only: bool = True
    account_id: str | None = None
    categories: tuple[str, ...] = ()
    tag: str | None = None


def query_spend_trends(
    connection: sqlite3.Connection,
    query: SpendTrendsQuery,
    today: date,
) -> list[dict[str, object]]:
    """Return monthly spend buckets, oldest → newest, zero-filled."""
    labels: list[str] = []
    year, month = today.year, today.month
    for _ in range(query.months):
        labels.append(f"{year}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    labels.reverse()

    start_date = f"{labels[0]}-01"
    end_date = today.isoformat()

    effective_date_sql = "COALESCE(t.posted_date, t.authorized_date)"

    need_accounts_join = query.canonical_only or query.owner is not None
    accounts_join = (
        "JOIN accounts a ON a.plaid_account_id = t.plaid_account_id "
        if need_accounts_join
        else ""
    )
    allocations_join = (
        "LEFT JOIN allocations alloc "
        "ON alloc.plaid_transaction_id = t.plaid_transaction_id "
    )

    where_parts: list[str] = [
        f"{effective_date_sql} >= ?",
        f"{effective_date_sql} <= ?",
    ]
    params: list[object] = [start_date, end_date]

    if query.canonical_only:
        where_parts.append("a.canonical_account_id IS NULL")

    if not query.include_pending:
        where_parts.append("t.pending = 0")

    if query.owner is not None:
        where_parts.append("a.owner = ?")
        params.append(query.owner)

    for tag in query.tags:
        where_parts.append(
            "EXISTS (SELECT 1 FROM json_each(alloc.tags) WHERE value = ?)"
        )
        params.append(tag)

    if query.account_id is not None:
        where_parts.append("t.plaid_account_id = ?")
        params.append(query.account_id)

    _apply_category_filters(query.categories, where_parts, params)

    if query.tag is not None:
        where_parts.append(
            "EXISTS ("
            "SELECT 1 FROM json_each(alloc.tags) WHERE LOWER(value) = LOWER(?)"
            ")"
        )
        params.append(query.tag)

    where_sql = " AND ".join(where_parts)
    month_expr = f"strftime('%Y-%m', {effective_date_sql})"
    rows = connection.execute(
        (
            f"SELECT {month_expr} AS month, "  # noqa: S608
            "COALESCE(SUM(alloc.amount), 0.0), COUNT(*) "
            "FROM transactions t "
            f"{accounts_join}"
            f"{allocations_join}"
            f"WHERE {where_sql} "
            "GROUP BY month "
            "ORDER BY month ASC"
        ),
        params,
    ).fetchall()

    current_month = f"{today.year}-{today.month:02d}"
    results: dict[str, tuple[float, int]] = {
        str(row[0]): (float(row[1]), int(row[2])) for row in rows
    }
    return [
        {
            "month": label,
            "total_spend": results.get(label, (0.0, 0))[0],
            "allocation_count": results.get(label, (0.0, 0))[1],
            "partial": label == current_month,
        }
        for label in labels
    ]


@dataclass(frozen=True)
class AccountLabelRow:
    """Normalized account label fields ready for SQL parameter binding."""

    plaid_account_id: str
    label: str | None
    description: str | None
    created_at: str
    updated_at: str


def get_all_accounts(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    """Return all accounts LEFT JOIN account_labels, ordered by account ID."""
    rows = connection.execute(
        "SELECT a.plaid_account_id, a.name, a.mask, a.type, a.subtype, "
        "a.institution_name, a.owner, a.item_id, a.canonical_account_id, "
        "al.label, al.description "
        "FROM accounts a "
        "LEFT JOIN account_labels al "
        "ON al.plaid_account_id = a.plaid_account_id "
        "ORDER BY a.plaid_account_id ASC"
    ).fetchall()
    return [
        {
            "account_id": str(row[0]),
            "plaid_name": str(row[1]),
            "mask": str(row[2]) if row[2] is not None else None,
            "type": str(row[3]) if row[3] is not None else None,
            "subtype": str(row[4]) if row[4] is not None else None,
            "institution_name": str(row[5]) if row[5] is not None else None,
            "owner": str(row[6]) if row[6] is not None else None,
            "item_id": str(row[7]) if row[7] is not None else None,
            "canonical_account_id": (
                str(row[8]) if row[8] is not None else None
            ),
            "label": str(row[9]) if row[9] is not None else None,
            "description": str(row[10]) if row[10] is not None else None,
        }
        for row in rows
    ]


def get_account(
    connection: sqlite3.Connection,
    plaid_account_id: str,
) -> dict[str, object] | None:
    """Return one account with label data, or None if not in accounts."""
    row = connection.execute(
        (
            "SELECT a.plaid_account_id, a.name, a.mask, a.type, a.subtype, "
            "a.institution_name, a.owner, a.item_id, a.canonical_account_id, "
            "al.label, al.description "
            "FROM accounts a "
            "LEFT JOIN account_labels al "
            "ON al.plaid_account_id = a.plaid_account_id "
            "WHERE a.plaid_account_id = ?"
        ),
        (plaid_account_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "account_id": str(row[0]),
        "plaid_name": str(row[1]),
        "mask": str(row[2]) if row[2] is not None else None,
        "type": str(row[3]) if row[3] is not None else None,
        "subtype": str(row[4]) if row[4] is not None else None,
        "institution_name": str(row[5]) if row[5] is not None else None,
        "owner": str(row[6]) if row[6] is not None else None,
        "item_id": str(row[7]) if row[7] is not None else None,
        "canonical_account_id": (str(row[8]) if row[8] is not None else None),
        "label": str(row[9]) if row[9] is not None else None,
        "description": str(row[10]) if row[10] is not None else None,
    }


def upsert_account_label(
    connection: sqlite3.Connection,
    row: AccountLabelRow,
) -> None:
    """Insert or update an account label keyed on plaid_account_id."""
    connection.execute(
        (
            "INSERT INTO account_labels "
            "(plaid_account_id, label, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(plaid_account_id) DO UPDATE SET "
            "label = excluded.label, "
            "description = excluded.description, "
            "updated_at = excluded.updated_at"
        ),
        (
            row.plaid_account_id,
            row.label,
            row.description,
            row.created_at,
            row.updated_at,
        ),
    )


def get_distinct_categories(connection: sqlite3.Connection) -> list[str]:
    """Return distinct non-null category values sorted alphabetically."""
    rows = connection.execute(
        "SELECT DISTINCT category FROM allocations "
        "WHERE category IS NOT NULL ORDER BY category COLLATE NOCASE"
    ).fetchall()
    return [str(row[0]) for row in rows]


def get_distinct_tags(connection: sqlite3.Connection) -> list[str]:
    """
    Return distinct tag values unnested from all allocation rows.

    Results are sorted alphabetically (case-insensitive).
    """
    rows = connection.execute(
        "SELECT DISTINCT j.value "
        "FROM allocations a, json_each(a.tags) j "
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


def update_plaid_item_id(
    connection: sqlite3.Connection,
    *,
    item_id: str,
    plaid_item_id: str,
) -> None:
    """Store the Plaid-assigned item ID on an existing sync_state row."""
    connection.execute(
        "UPDATE sync_state SET plaid_item_id = ? WHERE item_id = ?",
        (plaid_item_id, item_id),
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


def get_item_id_by_plaid_item_id(
    connection: sqlite3.Connection,
    plaid_item_id: str,
) -> str | None:
    """Return the logical item_id for a given plaid_item_id, or None."""
    row = connection.execute(
        "SELECT item_id FROM sync_state WHERE plaid_item_id = ?",
        (plaid_item_id,),
    ).fetchone()
    return str(row[0]) if row is not None else None


@dataclass(frozen=True)
class LedgerErrorQuery:
    """Filters for GET /errors."""

    hours: int = 24
    min_severity: str | None = None  # None / 'WARNING' → all; 'ERROR' → ERROR+
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True)
class LedgerErrorRow:
    """One row to insert into ledger_errors."""

    severity: str
    logger_name: str
    message: str
    correlation_id: str | None
    created_at: datetime


def insert_ledger_error(
    connection: sqlite3.Connection,
    row: LedgerErrorRow,
) -> None:
    """Insert one error row and prune rows older than 30 days."""
    connection.execute(
        "INSERT INTO ledger_errors "
        "(severity, logger_name, message, correlation_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            row.severity,
            row.logger_name,
            row.message,
            row.correlation_id,
            row.created_at.isoformat(),
        ),
    )
    cutoff = (row.created_at - timedelta(days=30)).isoformat()
    connection.execute(
        "DELETE FROM ledger_errors WHERE created_at < ?", (cutoff,)
    )


def query_ledger_errors(
    connection: sqlite3.Connection,
    query: LedgerErrorQuery,
) -> tuple[list[dict[str, object]], int]:
    """Return (rows, total) for the given filter window, newest first."""
    since_iso = (datetime.now(UTC) - timedelta(hours=query.hours)).isoformat()

    if query.min_severity == "ERROR":
        count_sql = (
            "SELECT COUNT(*) FROM ledger_errors"
            " WHERE created_at >= ?"
            " AND severity IN ('ERROR', 'CRITICAL')"
        )
        rows_sql = (
            "SELECT id, severity, logger_name, message,"
            " correlation_id, created_at FROM ledger_errors"
            " WHERE created_at >= ?"
            " AND severity IN ('ERROR', 'CRITICAL')"
            " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
    else:
        count_sql = "SELECT COUNT(*) FROM ledger_errors WHERE created_at >= ?"
        rows_sql = (
            "SELECT id, severity, logger_name, message,"
            " correlation_id, created_at FROM ledger_errors"
            " WHERE created_at >= ?"
            " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )

    total_row = connection.execute(count_sql, (since_iso,)).fetchone()
    total = int(total_row[0]) if total_row is not None else 0

    rows = connection.execute(
        rows_sql, (since_iso, query.limit, query.offset)
    ).fetchall()

    parsed_rows: list[dict[str, object]] = [
        {
            "id": int(row[0]),
            "severity": str(row[1]),
            "logger_name": str(row[2]),
            "message": str(row[3]),
            "correlation_id": str(row[4]) if row[4] is not None else None,
            "created_at": str(row[5]),
        }
        for row in rows
    ]

    return parsed_rows, total
