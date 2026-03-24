"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import fastapi

from claw_plaid_ledger.middleware.correlation import CorrelationIdMiddleware
from claw_plaid_ledger.middleware.ip_allowlist import (
    WebhookIPAllowlistMiddleware,
)
from claw_plaid_ledger.routers import accounts as accounts_module
from claw_plaid_ledger.routers import health as health_module
from claw_plaid_ledger.routers import spend as spend_module
from claw_plaid_ledger.routers import transactions as transactions_module
from claw_plaid_ledger.routers import webhooks as webhooks_module

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
app.include_router(accounts_module.router)
