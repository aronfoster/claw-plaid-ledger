"""Plaid client wrapper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from claw_plaid_ledger import plaid_client
from claw_plaid_ledger.config import Config
from claw_plaid_ledger.plaid_client import (
    PlaidClientConfigError,
    _resolve_plaid_host,
)


class _FakeConfiguration:
    def __init__(self, *, host: str, api_key: dict[str, str]) -> None:
        self.host = host
        self.api_key = api_key


class _FakeApiClient:
    def __init__(self, configuration: _FakeConfiguration) -> None:
        self.configuration = configuration


class _FakePlaidApi:
    def __init__(self, api_client: _FakeApiClient) -> None:
        self.api_client = api_client


class _FakePlaidApiModule:
    PlaidApi = _FakePlaidApi


def _config(*, env: str | None = "sandbox") -> Config:
    return Config(
        db_path=Path("ledger.db"),
        workspace_path=None,
        plaid_client_id="client-id",
        plaid_secret="dummy-token",  # noqa: S106
        plaid_env=env,
    )


def test_resolve_plaid_host_accepts_case_and_whitespace() -> None:
    """Known env aliases normalize to the expected host."""
    assert _resolve_plaid_host(" Sandbox ") == "https://sandbox.plaid.com"


def test_resolve_plaid_host_rejects_unknown_env() -> None:
    """Unknown PLAID_ENV values produce a clear error."""
    with pytest.raises(PlaidClientConfigError, match="Unsupported PLAID_ENV"):
        _resolve_plaid_host("qa")


def test_build_plaid_api_builds_configured_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper returns a PlaidApi instance configured with credentials."""
    monkeypatch.setattr(
        plaid_client,
        "_load_plaid_sdk",
        lambda: (_FakeApiClient, _FakeConfiguration, _FakePlaidApiModule),
    )

    client = plaid_client.build_plaid_api(_config())

    assert client.api_client.configuration.host == "https://sandbox.plaid.com"
    assert client.api_client.configuration.api_key["clientId"] == "client-id"
    assert client.api_client.configuration.api_key["secret"] == "dummy-token"  # noqa: S105


def test_build_plaid_api_requires_credentials() -> None:
    """Missing client ID or secret fails before building SDK objects."""
    config = Config(
        db_path=Path("ledger.db"),
        workspace_path=None,
        plaid_client_id=None,
        plaid_secret="dummy-token",  # noqa: S106
        plaid_env="sandbox",
    )

    with pytest.raises(
        PlaidClientConfigError, match="Missing Plaid credentials"
    ):
        plaid_client.build_plaid_api(config)
