"""Configuration loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from claw_plaid_ledger.config import ConfigError, load_config


def test_load_config_success_without_plaid_requirement() -> None:
    """Config loads DB path and optional values for non-sync commands."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "~/ledger/data.db",
            "CLAW_PLAID_LEDGER_WORKSPACE_PATH": "workspace",
        }
    )

    assert cfg.db_path == Path("~/ledger/data.db").expanduser()
    assert cfg.workspace_path == Path("workspace")
    assert cfg.plaid_client_id is None
    assert cfg.plaid_secret is None
    assert cfg.plaid_env is None
    assert cfg.plaid_access_token is None


def test_load_config_missing_db_path_raises_clear_error() -> None:
    """DB path is required for all current commands."""
    with pytest.raises(
        ConfigError,
        match=(
            r"Missing required environment variable\(s\): "
            r"CLAW_PLAID_LEDGER_DB_PATH"
        ),
    ):
        load_config({})


def test_load_config_requires_plaid_values_when_requested() -> None:
    """Plaid settings are validated only when explicitly required."""
    with pytest.raises(
        ConfigError,
        match=(
            r"Missing required environment variable\(s\): "
            r"PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV, PLAID_ACCESS_TOKEN"
        ),
    ):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            },
            require_plaid=True,
        )


def test_load_config_success_with_plaid_requirement() -> None:
    """Plaid settings are returned when provided and required."""
    access_value = "integration-fixture"
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "PLAID_CLIENT_ID": "client-id",
            "PLAID_SECRET": "token-value",
            "PLAID_ENV": "sandbox",
            "PLAID_ACCESS_TOKEN": access_value,
        },
        require_plaid=True,
    )

    assert cfg.db_path == Path("ledger.db")
    assert cfg.plaid_client_id == "client-id"
    assert cfg.plaid_secret is not None
    assert cfg.plaid_env == "sandbox"
    assert cfg.plaid_access_token == access_value
