"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import fastapi

app = fastapi.FastAPI(title="claw-plaid-ledger")


@app.get("/health")
def health() -> dict[str, str]:
    """Return service liveness status."""
    return {"status": "ok"}
