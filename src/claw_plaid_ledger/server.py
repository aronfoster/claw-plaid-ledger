"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Annotated

import fastapi
from fastapi import BackgroundTasks, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from claw_plaid_ledger.config import load_config
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
        run_sync(
            db_path=config.db_path,
            adapter=adapter,
            access_token=config.plaid_access_token,
            item_id=config.item_id,
        )
    except Exception:
        logger.exception("Background sync failed")


@app.get("/health")
def health() -> dict[str, str]:
    """Return service liveness status."""
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
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)
    webhook_type = payload.get("webhook_type", "")

    if webhook_type == _SYNC_UPDATES_AVAILABLE:
        background_tasks.add_task(_background_sync)
    else:
        logger.debug("Unrecognised Plaid webhook type: %s", webhook_type)

    return {"status": "ok"}
