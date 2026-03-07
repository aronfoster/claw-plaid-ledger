"""Thin Plaid SDK wrapper for sync-oriented operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from plaid import (  # type: ignore[import-untyped]
    ApiClient,
    Configuration,
    Environment,
)
from plaid.api import plaid_api  # type: ignore[import-untyped]
from plaid.model.transactions_sync_request import (  # type: ignore[import-untyped]
    TransactionsSyncRequest,
)

if TYPE_CHECKING:
    from plaid.model.transactions_sync_response import (  # type: ignore[import-untyped]
        TransactionsSyncResponse,
    )

    from claw_plaid_ledger.config import Config


class PlaidClientError(ValueError):
    """Raised when Plaid client settings are missing or invalid."""


_PLAID_ENVIRONMENTS: dict[str, str] = {
    "sandbox": Environment.Sandbox,
    "development": "https://development.plaid.com",
    "production": Environment.Production,
}


def build_plaid_api(config: Config) -> plaid_api.PlaidApi:
    """Create a Plaid API client from application configuration."""
    plaid_client_id = _require_value(config.plaid_client_id, "PLAID_CLIENT_ID")
    plaid_secret = _require_value(config.plaid_secret, "PLAID_SECRET")
    plaid_env = _require_value(config.plaid_env, "PLAID_ENV")

    host = _resolve_plaid_host(plaid_env)
    plaid_config = Configuration(
        host=host,
        api_key={
            "clientId": plaid_client_id,
            "secret": plaid_secret,
        },
    )
    api_client = ApiClient(plaid_config)
    return plaid_api.PlaidApi(api_client)


def transactions_sync(
    client: plaid_api.PlaidApi,
    access_token: str,
    cursor: str | None,
) -> TransactionsSyncResponse:
    """Fetch transaction deltas from Plaid using transactions/sync."""
    request = TransactionsSyncRequest(access_token=access_token, cursor=cursor)
    return client.transactions_sync(request)


def _require_value(value: str | None, name: str) -> str:
    """Return a required config value, rejecting missing and blank values."""
    if value is None or not value.strip():
        msg = f"{name} is required to construct a Plaid client"
        raise PlaidClientError(msg)
    return value


def _resolve_plaid_host(plaid_env: str) -> str:
    """Resolve supported Plaid environment names to SDK hosts."""
    normalized_env = plaid_env.strip().lower()
    host = _PLAID_ENVIRONMENTS.get(normalized_env)
    if host is None:
        allowed = ", ".join(sorted(_PLAID_ENVIRONMENTS))
        msg = (
            f"Unsupported PLAID_ENV {plaid_env!r}; expected one of: {allowed}"
        )
        raise PlaidClientError(msg)
    return host
