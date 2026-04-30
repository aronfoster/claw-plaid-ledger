"""Transaction and related endpoints."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from claw_plaid_ledger.config import load_config
from claw_plaid_ledger.db import (
    AllocationRow,
    TransactionQuery,
    get_allocations_for_transaction,
    get_transaction,
    query_transactions,
    replace_allocations,
)
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.routers.utils import (
    _resolve_spend_dates,
    _SpendRange,
    _strict_params,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_TRANSACTIONS_BASE_ALLOWED_PARAMS = frozenset(
    {
        "start_date",
        "end_date",
        "account_id",
        "pending",
        "min_amount",
        "max_amount",
        "keyword",
        "view",
        "limit",
        "offset",
        "search_notes",
        "tags",
        "range",
    }
)

_TRANSACTIONS_ALLOWED_PARAMS = _TRANSACTIONS_BASE_ALLOWED_PARAMS | {"category"}

_UNCATEGORIZED_ALLOWED_PARAMS = _TRANSACTIONS_BASE_ALLOWED_PARAMS


class TransactionListQuery(BaseModel):
    """Validated query parameters for the transactions list endpoint."""

    start_date: str | None = None
    end_date: str | None = None
    account_id: str | None = None
    pending: bool | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    keyword: str | None = None
    view: Literal["canonical", "raw"] = "canonical"
    limit: int = Query(default=100, le=500)
    offset: int = 0
    # bool | None avoids FBT001/FBT002; None is treated as False (default:
    # keyword searches do not include annotation notes unless opted in).
    search_notes: bool | None = None


@router.get(
    "/transactions",
    dependencies=[
        Depends(require_bearer_token),
        Depends(_strict_params(_TRANSACTIONS_ALLOWED_PARAMS)),
    ],
)
def list_transactions(
    params: Annotated[TransactionListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    date_range: Annotated[_SpendRange | None, Query(alias="range")] = None,
) -> dict[str, object]:
    """List transactions with optional filtering and pagination."""
    return _list_transactions_response(
        params=params,
        tags=tags,
        categories=category,
        date_range=date_range,
        mode="all",
    )


@router.get(
    "/transactions/uncategorized",
    dependencies=[
        Depends(require_bearer_token),
        Depends(_strict_params(_UNCATEGORIZED_ALLOWED_PARAMS)),
    ],
)
def list_uncategorized_transactions(
    params: Annotated[TransactionListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
    date_range: Annotated[_SpendRange | None, Query(alias="range")] = None,
) -> dict[str, object]:
    """List transactions whose joined allocation row has no category."""
    return _list_transactions_response(
        params=params,
        tags=tags,
        categories=None,
        date_range=date_range,
        mode="uncategorized",
    )


@router.get(
    "/transactions/splits",
    dependencies=[
        Depends(require_bearer_token),
        Depends(_strict_params(_TRANSACTIONS_ALLOWED_PARAMS)),
    ],
)
def list_split_transactions(
    params: Annotated[TransactionListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    date_range: Annotated[_SpendRange | None, Query(alias="range")] = None,
) -> dict[str, object]:
    """List all allocation rows for split transactions only."""
    return _list_transactions_response(
        params=params,
        tags=tags,
        categories=category,
        date_range=date_range,
        mode="splits",
    )


def _list_transactions_response(
    *,
    params: TransactionListQuery,
    tags: list[str] | None,
    categories: list[str] | None,
    date_range: _SpendRange | None,
    mode: Literal["all", "uncategorized", "splits"],
) -> dict[str, object]:
    """
    List transactions with optional filtering and pagination.

    Pass ``tags`` multiple times to require all listed tags (AND semantics).
    Set ``search_notes=true`` together with ``keyword`` to also match the
    annotation note field in addition to ``name`` and ``merchant_name``.

    ``range`` is an optional shorthand for common date windows
    (``this_month``, ``last_month``, ``last_30_days``, ``last_7_days``).
    Explicit ``start_date`` / ``end_date`` override the range-derived dates
    when both are provided together.
    """
    resolved_tags: tuple[str, ...] = tuple(tags) if tags else ()
    resolved_categories: tuple[str, ...] = (
        tuple(categories) if categories else ()
    )
    start_date = params.start_date
    end_date = params.end_date
    if date_range is not None:
        parsed_start = date.fromisoformat(start_date) if start_date else None
        parsed_end = date.fromisoformat(end_date) if end_date else None
        resolved_start, resolved_end = _resolve_spend_dates(
            date_range, parsed_start, parsed_end
        )
        start_date = resolved_start.isoformat()
        end_date = resolved_end.isoformat()
    config = load_config()
    query = TransactionQuery(
        start_date=start_date,
        end_date=end_date,
        account_id=params.account_id,
        pending=params.pending,
        min_amount=params.min_amount,
        max_amount=params.max_amount,
        keyword=params.keyword,
        canonical_only=params.view == "canonical",
        limit=params.limit,
        offset=params.offset,
        tags=resolved_tags,
        categories=resolved_categories,
        search_notes=params.search_notes is True,
        uncategorized_only=mode == "uncategorized",
        splits_only=mode == "splits",
    )
    with sqlite3.connect(config.db_path) as connection:
        rows, total = query_transactions(connection, query)
    return {
        "transactions": rows,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


def _fetch_transaction_with_allocation(
    connection: sqlite3.Connection,
    transaction_id: str,
) -> dict[str, object] | None:
    """Return a transaction merged with its first allocation, or None."""
    transaction = get_transaction(connection, transaction_id)
    if transaction is None:
        return None

    allocs = get_allocations_for_transaction(connection, transaction_id)
    alloc = allocs[0] if allocs else None

    allocation_payload: dict[str, object] | None = None
    if alloc is not None:
        allocation_payload = {
            "id": alloc.id,
            "amount": alloc.amount,
            "category": alloc.category,
            "note": alloc.note,
            "tags": json.loads(alloc.tags) if alloc.tags else None,
            "updated_at": alloc.updated_at,
        }

    return {**transaction, "allocation": allocation_payload}


def _fetch_transaction_with_allocations(
    connection: sqlite3.Connection,
    transaction_id: str,
) -> dict[str, object] | None:
    """Return a transaction merged with all its allocations, or None."""
    transaction = get_transaction(connection, transaction_id)
    if transaction is None:
        return None

    allocs = get_allocations_for_transaction(connection, transaction_id)
    allocations_payload: list[dict[str, object]] = [
        {
            "id": alloc.id,
            "amount": alloc.amount,
            "category": alloc.category,
            "note": alloc.note,
            "tags": json.loads(alloc.tags) if alloc.tags else None,
            "updated_at": alloc.updated_at,
        }
        for alloc in allocs
    ]
    return {**transaction, "allocations": allocations_payload}


@router.get(
    "/transactions/{transaction_id}",
    dependencies=[Depends(require_bearer_token)],
)
def get_transaction_detail(transaction_id: str) -> dict[str, object]:
    """Return one transaction with all its allocations."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        result = _fetch_transaction_with_allocations(
            connection, transaction_id
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


class AllocationItem(BaseModel):
    """One allocation item in a PUT /transactions/{id}/allocations request."""

    model_config = ConfigDict(extra="forbid")

    amount: float
    category: str | None = None
    tags: list[str] | None = None
    note: str | None = None


class BatchAllocationItem(BaseModel):
    """One allocation update item in POST /transactions/allocations/batch."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    category: str | None = None
    tags: list[str] | None = None
    note: str | None = None


class BatchAllocationFailedItem(BaseModel):
    """One failed update entry in a batch-allocation response."""

    transaction_id: str
    error: str


def _update_single_allocation_fields(
    connection: sqlite3.Connection,
    *,
    transaction_id: str,
    category: str | None,
    tags: list[str] | None,
    note: str | None,
) -> None:
    """Update category/tags/note on the single allocation for a transaction."""
    now = datetime.now(tz=UTC).isoformat()
    connection.execute(
        "UPDATE allocations "
        "SET category = ?, tags = ?, note = ?, updated_at = ? "
        "WHERE plaid_transaction_id = ?",
        (
            category,
            json.dumps(tags) if tags is not None else None,
            note,
            now,
            transaction_id,
        ),
    )


@router.post(
    "/transactions/allocations/batch",
    dependencies=[Depends(require_bearer_token)],
)
def post_transaction_allocations_batch(
    body: list[BatchAllocationItem],
) -> dict[str, object]:
    """Batch update single-allocation transactions with collect-all-errors."""
    if not body:
        raise HTTPException(
            status_code=422,
            detail={"error": "at least one allocation update is required"},
        )

    succeeded: list[str] = []
    failed: list[BatchAllocationFailedItem] = []
    seen_transaction_ids: set[str] = set()

    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        for item in body:
            if item.transaction_id in seen_transaction_ids:
                failed.append(
                    BatchAllocationFailedItem(
                        transaction_id=item.transaction_id,
                        error="duplicate transaction_id in batch",
                    )
                )
                continue
            seen_transaction_ids.add(item.transaction_id)

            try:
                transaction = get_transaction(connection, item.transaction_id)
                if transaction is None:
                    failed.append(
                        BatchAllocationFailedItem(
                            transaction_id=item.transaction_id,
                            error="transaction not found",
                        )
                    )
                    continue

                allocations = get_allocations_for_transaction(
                    connection, item.transaction_id
                )
                allocation_count = len(allocations)
                if allocation_count != 1:
                    failed.append(
                        BatchAllocationFailedItem(
                            transaction_id=item.transaction_id,
                            error=(
                                "split transaction "
                                f"({allocation_count} allocations); use "
                                "PUT /transactions/{id}/allocations"
                            ),
                        )
                    )
                    continue

                _update_single_allocation_fields(
                    connection,
                    transaction_id=item.transaction_id,
                    category=item.category,
                    tags=item.tags,
                    note=item.note,
                )
                connection.commit()
                succeeded.append(item.transaction_id)
            except sqlite3.Error as exc:
                connection.rollback()
                failed.append(
                    BatchAllocationFailedItem(
                        transaction_id=item.transaction_id,
                        error=f"database update failed: {exc}",
                    )
                )

    response: dict[str, object] = {"succeeded": succeeded}
    if failed:
        response["failed"] = [entry.model_dump() for entry in failed]
    return response


@router.put(
    "/transactions/{transaction_id}/allocations",
    dependencies=[Depends(require_bearer_token)],
)
def put_transaction_allocations(
    transaction_id: str, body: list[AllocationItem]
) -> dict[str, object]:
    """Atomically replace all allocations for a transaction."""
    if not body:
        raise HTTPException(
            status_code=422,
            detail={"error": "at least one allocation is required"},
        )

    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        transaction = get_transaction(connection, transaction_id)
        if transaction is None:
            raise HTTPException(
                status_code=404, detail="Transaction not found"
            )

        # Fetch amount via a direct SELECT so it is typed as Any (sqlite3
        # cursor row element) rather than object (dict value), avoiding a
        # type-narrowing issue without a type: ignore bypass.
        amount_row = connection.execute(
            "SELECT amount FROM transactions WHERE plaid_transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        tx_amount = round(float(amount_row[0]), 2)
        total = round(sum(item.amount for item in body), 2)
        diff = round(tx_amount - total, 2)

        if abs(diff) > 1.00:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "allocation amounts do not balance",
                    "transaction_amount": tx_amount,
                    "allocation_total": total,
                    "difference": diff,
                },
            )

        if diff != 0.0:
            body[-1].amount = round(body[-1].amount + diff, 2)

        now = datetime.now(tz=UTC).isoformat()
        alloc_rows = [
            AllocationRow(
                plaid_transaction_id=transaction_id,
                amount=item.amount,
                category=item.category,
                tags=json.dumps(item.tags) if item.tags is not None else None,
                note=item.note,
                created_at=now,
                updated_at=now,
            )
            for item in body
        ]
        replace_allocations(connection, transaction_id, alloc_rows)
        result = _fetch_transaction_with_allocations(
            connection, transaction_id
        )

    if result is None:
        # Should not happen: transaction was verified above.
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result
