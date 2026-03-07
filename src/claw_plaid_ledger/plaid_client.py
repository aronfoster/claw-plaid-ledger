"""Thin Plaid client construction boundary for sync workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plaid.api import plaid_api

    from claw_plaid_ledger.config import Config

_PLAID_HOSTS: dict[str, str] = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidClientConfigError(ValueError):
    """Raised when Plaid client configuration values are invalid."""


def _resolve_plaid_host(plaid_env: str | None) -> str:
    """Resolve a configured Plaid environment label to the API host URL."""
    if plaid_env is None:
        msg = "Missing PLAID_ENV for Plaid client construction"
        raise PlaidClientConfigError(msg)

    normalized = plaid_env.strip().lower()
    host = _PLAID_HOSTS.get(normalized)
    if host is None:
        supported = ", ".join(sorted(_PLAID_HOSTS))
        msg = (
            f"Unsupported PLAID_ENV value '{plaid_env}'. "
            f"Expected one of: {supported}"
        )
        raise PlaidClientConfigError(msg)

    return host


def _load_plaid_sdk() -> tuple[Any, Any, Any]:
    """Import Plaid SDK components when needed."""
    try:
        from plaid import ApiClient, Configuration  # noqa: PLC0415
        from plaid.api import plaid_api  # noqa: PLC0415
    except ModuleNotFoundError as error:
        msg = "plaid-python dependency is not installed"
        raise PlaidClientConfigError(msg) from error

    return ApiClient, Configuration, plaid_api


def build_plaid_api(config: Config) -> plaid_api.PlaidApi:
    """Construct a configured Plaid API client from runtime config."""
    if config.plaid_client_id is None or config.plaid_secret is None:
        msg = "Missing Plaid credentials in config"
        raise PlaidClientConfigError(msg)

    api_client, configuration, plaid_api = _load_plaid_sdk()
    api_config = configuration(
        host=_resolve_plaid_host(config.plaid_env),
        api_key={
            "clientId": config.plaid_client_id,
            "secret": config.plaid_secret,
        },
    )

    return plaid_api.PlaidApi(api_client(api_config))
