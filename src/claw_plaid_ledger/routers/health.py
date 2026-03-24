"""Health and error-log endpoints."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from claw_plaid_ledger.config import load_config
from claw_plaid_ledger.db import (
    LedgerErrorQuery,
    query_ledger_errors,
)
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.routers.utils import _strict_params

router = APIRouter()

_ERRORS_ALLOWED_PARAMS = frozenset(
    {"hours", "min_severity", "limit", "offset"}
)


@router.get("/health")
def health() -> dict[str, str]:
    """Return service liveness status."""
    return {"status": "ok"}


class ErrorListQuery(BaseModel):
    """Validated query parameters for the GET /errors endpoint."""

    hours: int = Field(default=24, ge=1)
    min_severity: Literal["WARNING", "ERROR"] | None = None
    limit: int = Query(default=100, le=500)
    offset: int = 0


@router.get(
    "/errors",
    dependencies=[
        Depends(require_bearer_token),
        Depends(_strict_params(_ERRORS_ALLOWED_PARAMS)),
    ],
)
def list_errors(
    params: Annotated[ErrorListQuery, Depends()],
) -> dict[str, object]:
    """Return recent ledger warnings and errors."""
    config = load_config()
    query = LedgerErrorQuery(
        hours=params.hours,
        min_severity=params.min_severity,
        limit=params.limit,
        offset=params.offset,
    )
    since = datetime.now(UTC) - timedelta(hours=params.hours)
    with sqlite3.connect(config.db_path) as connection:
        rows, total = query_ledger_errors(connection, query)
    return {
        "errors": rows,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
        "since": since.isoformat(),
    }
