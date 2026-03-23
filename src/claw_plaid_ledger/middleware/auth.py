"""Bearer token authentication dependency."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from claw_plaid_ledger.config import load_api_secret

_bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> None:
    """Enforce Authorization: Bearer <token> using CLAW_API_SECRET."""
    api_secret = load_api_secret()
    if not api_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if credentials is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not secrets.compare_digest(credentials.credentials, api_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
