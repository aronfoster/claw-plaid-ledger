"""Transaction, annotation, and related endpoints."""

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
    AnnotationRow,
    TransactionQuery,
    get_annotation,
    get_transaction,
    query_transactions,
    upsert_annotation,
)
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.routers.utils import (
    _resolve_spend_dates,
    _SpendRange,
    _strict_params,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_TRANSACTIONS_ALLOWED_PARAMS = frozenset(
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
    date_range: Annotated[_SpendRange | None, Query(alias="range")] = None,
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
        search_notes=params.search_notes is True,
    )
    with sqlite3.connect(config.db_path) as connection:
        rows, total = query_transactions(connection, query)
    return {
        "transactions": rows,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


def _fetch_transaction_with_annotation(
    connection: sqlite3.Connection,
    transaction_id: str,
) -> dict[str, object] | None:
    """Return a transaction merged with its annotation, or None if absent."""
    transaction = get_transaction(connection, transaction_id)
    if transaction is None:
        return None

    annotation = get_annotation(connection, transaction_id)

    annotation_payload: dict[str, object] | None = None
    if annotation is not None:
        tags: list[str] | None = None
        if annotation.tags is not None:
            tags_raw = json.loads(annotation.tags)
            tags = [str(tag) for tag in tags_raw]

        annotation_payload = {
            "category": annotation.category,
            "note": annotation.note,
            "tags": tags,
            "updated_at": annotation.updated_at,
        }

    return {**transaction, "annotation": annotation_payload}


@router.get(
    "/transactions/{transaction_id}",
    dependencies=[Depends(require_bearer_token)],
)
def get_transaction_detail(transaction_id: str) -> dict[str, object]:
    """Return one transaction with optional merged annotation."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        result = _fetch_transaction_with_annotation(connection, transaction_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


class AnnotationRequest(BaseModel):
    """Request body for PUT /annotations/{transaction_id}."""

    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    note: str | None = None
    tags: list[str] | None = None


@router.put(
    "/annotations/{transaction_id}",
    dependencies=[Depends(require_bearer_token)],
)
def put_annotation(
    transaction_id: str, body: AnnotationRequest
) -> dict[str, object]:
    """Create or fully replace an annotation for a transaction."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        if get_transaction(connection, transaction_id) is None:
            raise HTTPException(
                status_code=404, detail="Transaction not found"
            )
        now = datetime.now(tz=UTC).isoformat()
        existing = get_annotation(connection, transaction_id)
        created_at = existing.created_at if existing is not None else now
        tags_json = json.dumps(body.tags) if body.tags is not None else None
        row = AnnotationRow(
            plaid_transaction_id=transaction_id,
            category=body.category,
            note=body.note,
            tags=tags_json,
            created_at=created_at,
            updated_at=now,
        )
        upsert_annotation(connection, row)
        logger.debug(
            "annotation upserted transaction_id=%s category=%r tags=%s",
            transaction_id,
            body.category,
            body.tags,
        )
        result = _fetch_transaction_with_annotation(connection, transaction_id)
    if result is None:
        # Should not happen: we verified the transaction exists above.
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result
