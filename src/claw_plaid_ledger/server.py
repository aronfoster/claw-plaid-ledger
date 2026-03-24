"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime
from typing import Annotated, Literal

import fastapi
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from claw_plaid_ledger.config import load_config
from claw_plaid_ledger.db import (
    AccountLabelRow,
    AnnotationRow,
    SpendQuery,
    SpendTrendsQuery,
    TransactionQuery,
    get_account,
    get_all_accounts,
    get_annotation,
    get_distinct_categories,
    get_distinct_tags,
    get_transaction,
    query_spend,
    query_spend_trends,
    query_transactions,
    upsert_account_label,
    upsert_annotation,
)
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.middleware.correlation import CorrelationIdMiddleware
from claw_plaid_ledger.middleware.ip_allowlist import (
    WebhookIPAllowlistMiddleware,
)
from claw_plaid_ledger.routers import health as health_module
from claw_plaid_ledger.routers import webhooks as webhooks_module
from claw_plaid_ledger.routers.utils import (
    _resolve_spend_dates,
    _SpendRange,
    _today,
)

logger = logging.getLogger(__name__)

app = fastapi.FastAPI(
    title="claw-plaid-ledger", lifespan=webhooks_module.lifespan
)
# Middleware is applied in reverse registration order (last added = outermost).
# CorrelationIdMiddleware must be outermost so request_id is set before the
# allowlist middleware logs its WARNING.
app.add_middleware(WebhookIPAllowlistMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.include_router(webhooks_module.router)
app.include_router(health_module.router)


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


@app.get("/transactions", dependencies=[Depends(require_bearer_token)])
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


class SpendListQuery(BaseModel):
    """
    Scalar query parameters for GET /spend.

    List-typed params (tags) and bool params (include_pending) are declared
    separately on the endpoint to satisfy FastAPI's multi-value and
    FBT001/FBT002 constraints.  ``start_date`` and ``end_date`` are optional
    because they may be derived from the ``range`` shorthand parameter.
    """

    start_date: date | None = None
    end_date: date | None = None
    owner: str | None = None
    # bool | None avoids FBT001/FBT002; None is treated as False (conservative
    # default: exclude pending transactions unless caller opts in).
    include_pending: bool | None = None
    view: Literal["canonical", "raw"] = "canonical"
    account_id: str | None = None
    category: str | None = None
    tag: str | None = None


@app.get("/spend", dependencies=[Depends(require_bearer_token)])
def get_spend(
    params: Annotated[SpendListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
    date_range: Annotated[_SpendRange | None, Query(alias="range")] = None,
) -> dict[str, object]:
    """
    Return aggregate spend totals for a date window with optional filters.

    Sums transaction amounts over the inclusive date window.  Positive amounts
    are debits (money leaving the account); negative amounts are credits —
    the sum is returned as-is per Plaid conventions.  Pass ``tags`` multiple
    times to require all listed tags (AND semantics).

    ``range`` is an optional shorthand for common date windows
    (``this_month``, ``last_month``, ``last_30_days``, ``last_7_days``).
    Explicit ``start_date`` / ``end_date`` override the range-derived dates
    when both are provided together.  If ``range`` is absent, both
    ``start_date`` and ``end_date`` are required.
    """
    resolved_start, resolved_end = _resolve_spend_dates(
        date_range, params.start_date, params.end_date
    )
    resolved_tags: list[str] = tags or []
    include_pending = params.include_pending is True
    config = load_config()
    spend_query = SpendQuery(
        start_date=resolved_start.isoformat(),
        end_date=resolved_end.isoformat(),
        owner=params.owner,
        tags=tuple(resolved_tags),
        include_pending=include_pending,
        canonical_only=params.view == "canonical",
        account_id=params.account_id,
        category=params.category,
        tag=params.tag,
    )
    with sqlite3.connect(config.db_path) as connection:
        total_spend, transaction_count = query_spend(connection, spend_query)
    return {
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "total_spend": total_spend,
        "transaction_count": transaction_count,
        "includes_pending": include_pending,
        "filters": {
            "owner": params.owner,
            "tags": resolved_tags,
            "account_id": params.account_id,
            "category": params.category,
            "tag": params.tag,
        },
    }


class SpendTrendsListQuery(BaseModel):
    """Scalar query parameters for GET /spend/trends."""

    months: int = Field(default=6, ge=1)
    owner: str | None = None
    # bool | None avoids FBT001/FBT002; None is treated as False (conservative
    # default: exclude pending transactions unless caller opts in).
    include_pending: bool | None = None
    view: Literal["canonical", "raw"] = "canonical"
    account_id: str | None = None
    category: str | None = None
    tag: str | None = None


@app.get("/spend/trends", dependencies=[Depends(require_bearer_token)])
def get_spend_trends(
    params: Annotated[SpendTrendsListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
) -> list[dict[str, object]]:
    """
    Return spend aggregated by calendar month for a lookback window.

    Returns exactly ``months`` buckets ordered oldest → newest. The
    current (in-progress) calendar month is flagged ``partial: true``
    so callers know not to compare it directly against complete months.
    Months with no qualifying transactions appear as zero-filled buckets.

    Supports the same filters as ``GET /spend`` (``owner``, ``tags``,
    ``category``, ``tag``, ``account_id``, ``view``,
    ``include_pending``) for direct comparability.
    """
    resolved_tags: list[str] = tags or []
    include_pending = params.include_pending is True
    config = load_config()
    trends_query = SpendTrendsQuery(
        months=params.months,
        owner=params.owner,
        tags=tuple(resolved_tags),
        include_pending=include_pending,
        canonical_only=params.view == "canonical",
        account_id=params.account_id,
        category=params.category,
        tag=params.tag,
    )
    with sqlite3.connect(config.db_path) as connection:
        return query_spend_trends(connection, trends_query, _today())


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


@app.get(
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

    category: str | None = None
    note: str | None = None
    tags: list[str] | None = None


@app.put(
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


@app.get("/categories", dependencies=[Depends(require_bearer_token)])
def get_categories() -> dict[str, object]:
    """Return distinct non-null category values from annotations, sorted."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        categories = get_distinct_categories(connection)
    return {"categories": categories}


@app.get("/tags", dependencies=[Depends(require_bearer_token)])
def get_tags() -> dict[str, object]:
    """Return distinct tag values unnested from all annotations, sorted."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        tags = get_distinct_tags(connection)
    return {"tags": tags}


@app.get("/accounts", dependencies=[Depends(require_bearer_token)])
def list_accounts() -> dict[str, object]:
    """Return all known accounts joined with any available label data."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        accounts = get_all_accounts(connection)
    return {"accounts": accounts}


class AccountLabelRequest(BaseModel):
    """Request body for PUT /accounts/{account_id}."""

    label: str | None = None
    description: str | None = None


@app.put(
    "/accounts/{account_id}",
    dependencies=[Depends(require_bearer_token)],
)
def put_account_label(
    account_id: str, body: AccountLabelRequest
) -> dict[str, object]:
    """Upsert label data for an account and return the full account record."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        if get_account(connection, account_id) is None:
            raise HTTPException(status_code=404, detail="Account not found")
        now = datetime.now(tz=UTC).isoformat()
        row = AccountLabelRow(
            plaid_account_id=account_id,
            label=body.label,
            description=body.description,
            created_at=now,
            updated_at=now,
        )
        upsert_account_label(connection, row)
        result = get_account(connection, account_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return result
