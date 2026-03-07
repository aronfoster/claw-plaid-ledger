"""Plaid client wrapper tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from claw_plaid_ledger.config import Config
from claw_plaid_ledger.plaid_client import (
    PlaidClientError,
    build_plaid_api,
    transactions_sync,
)

_DEFAULT_TEST_CREDENTIAL = "not-a-real-secret"


def _config(
    *,
    plaid_client_id: str | None = "client-id",
    plaid_secret: str | None = _DEFAULT_TEST_CREDENTIAL,
    plaid_env: str | None = "sandbox",
) -> Config:
    return Config(
        db_path=Path("ledger.db"),
        workspace_path=None,
        plaid_client_id=plaid_client_id,
        plaid_secret=plaid_secret,
        plaid_env=plaid_env,
    )


def test_build_plaid_api_constructs_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plaid API creation uses resolved host and configured credentials."""
    captured: dict[str, Any] = {}

    def fake_configuration(
        *, host: str, api_key: dict[str, str]
    ) -> dict[str, Any]:
        captured["host"] = host
        captured["api_key"] = api_key
        return {"host": host, "api_key": api_key}

    def fake_api_client(configuration: dict[str, Any]) -> dict[str, Any]:
        captured["configuration"] = configuration
        return {"configuration": configuration}

    class FakePlaidApi:
        def __init__(self, api_client: dict[str, Any]) -> None:
            captured["api_client"] = api_client

    monkeypatch.setattr(
        "claw_plaid_ledger.plaid_client.Configuration", fake_configuration
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.plaid_client.ApiClient", fake_api_client
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.plaid_client.plaid_api.PlaidApi", FakePlaidApi
    )

    api = build_plaid_api(_config(plaid_env="  SANDBOX  "))

    assert isinstance(api, FakePlaidApi)
    assert captured["host"] == "https://sandbox.plaid.com"
    assert captured["api_key"] == {
        "clientId": "client-id",
        "secret": _DEFAULT_TEST_CREDENTIAL,
    }


@pytest.mark.parametrize(
    ("field", "field_value", "expected_message"),
    [
        ("plaid_client_id", None, "PLAID_CLIENT_ID is required"),
        ("plaid_client_id", "", "PLAID_CLIENT_ID is required"),
        ("plaid_secret", None, "PLAID_SECRET is required"),
        ("plaid_secret", "   ", "PLAID_SECRET is required"),
        ("plaid_env", None, "PLAID_ENV is required"),
        ("plaid_env", "", "PLAID_ENV is required"),
    ],
)
def test_build_plaid_api_validates_required_values(
    field: str,
    field_value: str | None,
    expected_message: str,
) -> None:
    """Missing or blank Plaid values fail fast with clear guidance."""
    cfg = _config()
    cfg_kwargs = cfg.__dict__.copy()
    cfg_kwargs[field] = field_value

    with pytest.raises(PlaidClientError, match=expected_message):
        build_plaid_api(Config(**cfg_kwargs))


def test_build_plaid_api_rejects_unknown_environment() -> None:
    """Only known Plaid environment names are accepted."""
    with pytest.raises(PlaidClientError, match="Unsupported PLAID_ENV"):
        build_plaid_api(_config(plaid_env="qa"))


def test_transactions_sync_builds_request_and_calls_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transactions_sync builds the request model and delegates to the SDK."""
    captured: dict[str, Any] = {}

    def fake_request(
        *, access_token: str, cursor: str | None
    ) -> dict[str, Any]:
        captured["access_token"] = access_token
        captured["cursor"] = cursor
        return {"access_token": access_token, "cursor": cursor}

    sentinel_response = object()

    class FakeClient:
        def transactions_sync(self, request: dict[str, Any]) -> object:
            captured["request"] = request
            return sentinel_response

    monkeypatch.setattr(
        "claw_plaid_ledger.plaid_client.TransactionsSyncRequest",
        fake_request,
    )

    client = cast("Any", FakeClient())
    result = transactions_sync(client, "access-token", None)

    assert result is sentinel_response
    assert captured == {
        "access_token": "access-token",
        "cursor": None,
        "request": {"access_token": "access-token", "cursor": None},
    }
