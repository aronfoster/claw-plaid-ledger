"""Spend and spend-trends endpoints."""

from __future__ import annotations

import sqlite3

# TC003: datetime.date is used as a Pydantic field type (date | None).
# Moving it to TYPE_CHECKING breaks Pydantic v2's runtime annotation
# resolution: Pydantic calls get_type_hints() using the module's globals at
# model-build time, so the type must be importable at runtime. This will be
# removable if Pydantic gains a way to resolve TYPE_CHECKING imports without
# requiring the import in module globals.
from datetime import date  # noqa: TC003
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from claw_plaid_ledger.config import load_config
from claw_plaid_ledger.db import (
    SpendQuery,
    SpendTrendsQuery,
    query_spend,
    query_spend_trends,
)
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.routers.utils import (
    _resolve_spend_dates,
    _SpendRange,
    _strict_params,
    _today,
)

router = APIRouter()

_SPEND_ALLOWED_PARAMS = frozenset(
    {
        "start_date",
        "end_date",
        "owner",
        "tags",
        "include_pending",
        "view",
        "account_id",
        "category",
        "tag",
        "range",
    }
)

_SPEND_TRENDS_ALLOWED_PARAMS = frozenset(
    {
        "months",
        "owner",
        "tags",
        "include_pending",
        "view",
        "account_id",
        "category",
        "tag",
    }
)


class SpendListQuery(BaseModel):
    """
    Scalar query parameters for GET /spend.

    List-typed params (tags, category) and bool params (include_pending) are
    declared separately on the endpoint to satisfy FastAPI's multi-value and
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
    tag: str | None = None


@router.get(
    "/spend",
    dependencies=[
        Depends(require_bearer_token),
        Depends(_strict_params(_SPEND_ALLOWED_PARAMS)),
    ],
)
def get_spend(
    params: Annotated[SpendListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
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
    resolved_categories: list[str] = category or []
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
        categories=tuple(resolved_categories),
        tag=params.tag,
    )
    with sqlite3.connect(config.db_path) as connection:
        total_spend, allocation_count = query_spend(connection, spend_query)
    legacy_category = (
        resolved_categories[0] if len(resolved_categories) == 1 else None
    )
    return {
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "total_spend": total_spend,
        "allocation_count": allocation_count,
        "includes_pending": include_pending,
        "filters": {
            "owner": params.owner,
            "tags": resolved_tags,
            "account_id": params.account_id,
            "category": legacy_category,
            "categories": resolved_categories,
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
    tag: str | None = None


@router.get(
    "/spend/trends",
    dependencies=[
        Depends(require_bearer_token),
        Depends(_strict_params(_SPEND_TRENDS_ALLOWED_PARAMS)),
    ],
)
def get_spend_trends(
    params: Annotated[SpendTrendsListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
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
    resolved_categories: list[str] = category or []
    include_pending = params.include_pending is True
    config = load_config()
    trends_query = SpendTrendsQuery(
        months=params.months,
        owner=params.owner,
        tags=tuple(resolved_tags),
        include_pending=include_pending,
        canonical_only=params.view == "canonical",
        account_id=params.account_id,
        categories=tuple(resolved_categories),
        tag=params.tag,
    )
    with sqlite3.connect(config.db_path) as connection:
        return query_spend_trends(connection, trends_query, _today())
