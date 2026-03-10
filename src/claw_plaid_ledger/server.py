"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Annotated

import fastapi
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from claw_plaid_ledger.config import load_config
from claw_plaid_ledger.db import (
    AnnotationRow,
    TransactionQuery,
    get_annotation,
    get_transaction,
    query_transactions,
    upsert_annotation,
)
from claw_plaid_ledger.plaid_adapter import PlaidClientAdapter
from claw_plaid_ledger.sync_engine import run_sync
from claw_plaid_ledger.webhook_auth import verify_plaid_signature

app = fastapi.FastAPI(title="claw-plaid-ledger")

_bearer_scheme = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)

_SYNC_UPDATES_AVAILABLE = "SYNC_UPDATES_AVAILABLE"


def require_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> None:
    """Enforce Authorization: Bearer <token> using CLAW_API_SECRET."""
    api_secret = os.environ.get("CLAW_API_SECRET")
    if not api_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if credentials is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not secrets.compare_digest(credentials.credentials, api_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _background_sync() -> None:
    """Load config, build adapter, and run sync; log errors without raising."""
    try:
        config = load_config(require_plaid=True)
        if not config.plaid_access_token:
            logger.error("Background sync aborted: PLAID_ACCESS_TOKEN not set")
            return
        adapter = PlaidClientAdapter.from_config(config)
        logger.info("Background sync starting item_id=%s", config.item_id)
        summary = run_sync(
            db_path=config.db_path,
            adapter=adapter,
            access_token=config.plaid_access_token,
            item_id=config.item_id,
        )
        logger.info(
            "Background sync completed accounts=%d added=%d modified=%d"
            " removed=%d",
            summary.accounts,
            summary.added,
            summary.modified,
            summary.removed,
        )
        if (
            summary.added == 0
            and summary.modified == 0
            and summary.removed == 0
        ):
            logger.warning(
                "Background sync completed with zero transaction changes; "
                "verify webhook payload and Plaid item state"
            )
    except Exception:
        logger.exception("Background sync failed")


@app.get("/health")
def health() -> dict[str, str]:
    """Return service liveness status."""
    return {"status": "ok"}


class TransactionListQuery(BaseModel):
    """Validated query parameters for the transactions list endpoint."""

    start_date: str | None = None
    end_date: str | None = None
    account_id: str | None = None
    pending: bool | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    keyword: str | None = None
    limit: int = Query(default=100, le=500)
    offset: int = 0


@app.get("/transactions", dependencies=[Depends(require_bearer_token)])
def list_transactions(
    params: Annotated[TransactionListQuery, Depends()],
) -> dict[str, object]:
    """List transactions with optional filtering and pagination."""
    config = load_config()
    query = TransactionQuery(
        start_date=params.start_date,
        end_date=params.end_date,
        account_id=params.account_id,
        pending=params.pending,
        min_amount=params.min_amount,
        max_amount=params.max_amount,
        keyword=params.keyword,
        limit=params.limit,
        offset=params.offset,
    )
    with sqlite3.connect(config.db_path) as connection:
        rows, total = query_transactions(connection, query)
    return {
        "transactions": rows,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@app.get(
    "/transactions/{transaction_id}",
    dependencies=[Depends(require_bearer_token)],
)
def get_transaction_detail(transaction_id: str) -> dict[str, object]:
    """Return one transaction with optional merged annotation."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        transaction = get_transaction(connection, transaction_id)
        if transaction is None:
            raise HTTPException(
                status_code=404, detail="Transaction not found"
            )

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
) -> dict[str, str]:
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
    return {"status": "ok"}


@app.post("/webhooks/plaid", dependencies=[Depends(require_bearer_token)])
async def webhook_plaid(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Handle Plaid webhook events."""
    body = await request.body()
    headers = dict(request.headers)

    if not verify_plaid_signature(body, headers):
        logger.error("Plaid webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)
    webhook_type = payload.get("webhook_type", "")
    logger.info("Plaid webhook received webhook_type=%s", webhook_type)

    if webhook_type == _SYNC_UPDATES_AVAILABLE:
        logger.info(
            "Enqueuing background sync for webhook_type=%s", webhook_type
        )
        background_tasks.add_task(_background_sync)
    else:
        logger.warning("Unrecognised Plaid webhook type: %s", webhook_type)

    return {"status": "ok"}
