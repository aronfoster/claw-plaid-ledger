"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

import fastapi
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from claw_plaid_ledger.config import load_config
from claw_plaid_ledger.db import (
    AccountLabelRow,
    get_account,
    get_all_accounts,
    get_distinct_categories,
    get_distinct_tags,
    upsert_account_label,
)
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.middleware.correlation import CorrelationIdMiddleware
from claw_plaid_ledger.middleware.ip_allowlist import (
    WebhookIPAllowlistMiddleware,
)
from claw_plaid_ledger.routers import health as health_module
from claw_plaid_ledger.routers import spend as spend_module
from claw_plaid_ledger.routers import transactions as transactions_module
from claw_plaid_ledger.routers import webhooks as webhooks_module

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
app.include_router(spend_module.router)
app.include_router(transactions_module.router)


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
