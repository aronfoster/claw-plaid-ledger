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


def test_load_config_reads_values_from_env_file(tmp_path: Path) -> None:
    """Config can load values from a per-user .env file."""
    expected_credential = "test-plaid-value"
    expected_access = "test-access-value"
    env_file = tmp_path / ".env"
    env_file.write_text(
        (
            "CLAW_PLAID_LEDGER_DB_PATH=~/ledger/data.db\n"
            "CLAW_PLAID_LEDGER_WORKSPACE_PATH=~/workspace\n"
            "PLAID_CLIENT_ID=client-id\n"
            f"PLAID_SECRET={expected_credential}\n"
            "PLAID_ENV=sandbox\n"
            f"PLAID_ACCESS_TOKEN={expected_access}\n"
        ),
        encoding="utf-8",
    )

    cfg = load_config({}, require_plaid=True, env_file=env_file)

    assert cfg.db_path == Path("~/ledger/data.db").expanduser()
    assert cfg.workspace_path == Path("~/workspace").expanduser()
    assert cfg.plaid_client_id == "client-id"
    assert cfg.plaid_secret == expected_credential
    assert cfg.plaid_env == "sandbox"
    assert cfg.plaid_access_token == expected_access


def test_load_config_prefers_runtime_environment_over_env_file(
    tmp_path: Path,
) -> None:
    """Runtime environment variables override values from .env files."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        ("CLAW_PLAID_LEDGER_DB_PATH=from-file.db\nPLAID_ENV=development\n"),
        encoding="utf-8",
    )

    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "from-runtime.db",
            "PLAID_ENV": "sandbox",
        },
        env_file=env_file,
    )

    assert cfg.db_path == Path("from-runtime.db")
    assert cfg.plaid_env == "sandbox"


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
