"""Account, category, and tag endpoints."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

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

logger = logging.getLogger(__name__)

router = APIRouter()


class AccountLabelRequest(BaseModel):
    """Request body for PUT /accounts/{account_id}."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    description: str | None = None


@router.get("/categories", dependencies=[Depends(require_bearer_token)])
def get_categories() -> dict[str, object]:
    """Return distinct non-null category values from annotations, sorted."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        categories = get_distinct_categories(connection)
    return {"categories": categories}


@router.get("/tags", dependencies=[Depends(require_bearer_token)])
def get_tags() -> dict[str, object]:
    """Return distinct tag values unnested from all annotations, sorted."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        tags = get_distinct_tags(connection)
    return {"tags": tags}


@router.get("/accounts", dependencies=[Depends(require_bearer_token)])
def list_accounts() -> dict[str, object]:
    """Return all known accounts joined with any available label data."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        accounts = get_all_accounts(connection)
    return {"accounts": accounts}


@router.put(
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
