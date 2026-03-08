"""Thin Plaid API client wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

import plaid
from plaid.api import plaid_api

from claw_plaid_ledger.config import Config, ConfigError

if TYPE_CHECKING:
    from plaid.api.plaid_api import PlaidApi

_ENV_MAP: dict[str, str] = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

SUPPORTED_ENVIRONMENTS: frozenset[str] = frozenset(_ENV_MAP)


def _resolve_host(plaid_env: str) -> str:
    """Map a PLAID_ENV string to a Plaid base URL."""
    try:
        return _ENV_MAP[plaid_env.lower()]
    except KeyError:
        supported = ", ".join(sorted(_ENV_MAP))
        msg = (
            f"Unsupported PLAID_ENV '{plaid_env}'. "
            f"Supported values: {supported}"
        )
        raise ConfigError(msg) from None


def build_plaid_api(config: Config) -> PlaidApi:
    """Construct a PlaidApi from application configuration."""
    if not config.plaid_client_id:
        raise ConfigError.for_missing_env_vars(["PLAID_CLIENT_ID"])
    if not config.plaid_secret:
        raise ConfigError.for_missing_env_vars(["PLAID_SECRET"])
    if not config.plaid_env:
        raise ConfigError.for_missing_env_vars(["PLAID_ENV"])

    host = _resolve_host(config.plaid_env)
    configuration = plaid.Configuration(host=host)
    api_client = plaid.ApiClient(configuration=configuration)
    api_client.set_default_header("PLAID-CLIENT-ID", config.plaid_client_id)
    api_client.set_default_header("PLAID-SECRET", config.plaid_secret)
    return plaid_api.PlaidApi(api_client)
