"""Tests for the Plaid client wrapper."""

from __future__ import annotations

from pathlib import Path

import plaid
import pytest
from plaid.api.plaid_api import PlaidApi

from claw_plaid_ledger.config import Config, ConfigError
from claw_plaid_ledger.plaid_client import (
    SUPPORTED_ENVIRONMENTS,
    build_plaid_api,
)


def _make_config(
    *,
    plaid_client_id: str | None = "test-client-id",
    plaid_secret: str | None = "test-secret",  # noqa: S107
    plaid_env: str | None = "sandbox",
) -> Config:
    """Return a Config with valid Plaid credentials by default."""
    return Config(
        db_path=Path("ledger.db"),
        workspace_path=None,
        plaid_client_id=plaid_client_id,
        plaid_secret=plaid_secret,
        plaid_env=plaid_env,
    )


def test_supported_environments_contains_expected() -> None:
    """SUPPORTED_ENVIRONMENTS includes sandbox and production."""
    assert "sandbox" in SUPPORTED_ENVIRONMENTS
    assert "production" in SUPPORTED_ENVIRONMENTS


def test_build_plaid_api_sandbox_returns_plaid_api() -> None:
    """build_plaid_api returns a PlaidApi instance for the sandbox env."""
    api = build_plaid_api(_make_config())
    assert isinstance(api, PlaidApi)


def test_build_plaid_api_production_returns_plaid_api() -> None:
    """build_plaid_api returns a PlaidApi instance for the production env."""
    api = build_plaid_api(_make_config(plaid_env="production"))
    assert isinstance(api, PlaidApi)


def test_build_plaid_api_sets_auth_headers() -> None:
    """build_plaid_api writes credentials into the api_client headers."""
    secret = "my-secret"  # noqa: S105
    api = build_plaid_api(
        _make_config(plaid_client_id="my-id", plaid_secret=secret)
    )
    headers = api.api_client.default_headers
    assert headers.get("PLAID-CLIENT-ID") == "my-id"
    assert headers.get("PLAID-SECRET") == secret


def test_build_plaid_api_sandbox_host() -> None:
    """build_plaid_api points the client at the Plaid sandbox base URL."""
    api = build_plaid_api(_make_config(plaid_env="sandbox"))
    assert api.api_client.configuration.host == plaid.Environment.Sandbox


def test_build_plaid_api_production_host() -> None:
    """build_plaid_api points the client at the Plaid production base URL."""
    api = build_plaid_api(_make_config(plaid_env="production"))
    assert api.api_client.configuration.host == plaid.Environment.Production


def test_build_plaid_api_missing_client_id_raises() -> None:
    """build_plaid_api raises ConfigError when PLAID_CLIENT_ID is absent."""
    with pytest.raises(ConfigError, match="PLAID_CLIENT_ID"):
        build_plaid_api(_make_config(plaid_client_id=None))


def test_build_plaid_api_missing_secret_raises() -> None:
    """build_plaid_api raises ConfigError when PLAID_SECRET is absent."""
    with pytest.raises(ConfigError, match="PLAID_SECRET"):
        build_plaid_api(_make_config(plaid_secret=None))


def test_build_plaid_api_missing_env_raises() -> None:
    """build_plaid_api raises ConfigError when PLAID_ENV is absent."""
    with pytest.raises(ConfigError, match="PLAID_ENV"):
        build_plaid_api(_make_config(plaid_env=None))


def test_build_plaid_api_unsupported_env_raises() -> None:
    """build_plaid_api raises ConfigError for an unrecognized PLAID_ENV."""
    with pytest.raises(
        ConfigError, match="Unsupported PLAID_ENV 'development'"
    ):
        build_plaid_api(_make_config(plaid_env="development"))


def test_build_plaid_api_env_matching_is_case_insensitive() -> None:
    """PLAID_ENV values are matched case-insensitively."""
    api = build_plaid_api(_make_config(plaid_env="Sandbox"))
    assert isinstance(api, PlaidApi)
