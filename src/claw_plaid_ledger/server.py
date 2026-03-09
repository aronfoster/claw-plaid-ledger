"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import os
import secrets
from typing import Annotated

import fastapi
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

app = fastapi.FastAPI(title="claw-plaid-ledger")

_bearer_scheme = HTTPBearer(auto_error=False)


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


@app.get("/health")
def health() -> dict[str, str]:
    """Return service liveness status."""
    return {"status": "ok"}
