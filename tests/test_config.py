"""Configuration loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from claw_plaid_ledger.config import DEFAULT_ITEM_ID, ConfigError, load_config


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


def test_load_config_require_plaid_client_missing_client_id() -> None:
    """require_plaid_client validates shared Plaid client credentials."""
    with pytest.raises(
        ConfigError,
        match=(
            r"Missing required environment variable\(s\): "
            r"PLAID_CLIENT_ID"
        ),
    ):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "PLAID_SECRET": "secret",
                "PLAID_ENV": "sandbox",
            },
            require_plaid_client=True,
        )


def test_load_config_require_plaid_client_does_not_require_access_token() -> (
    None
):
    """require_plaid_client does not require PLAID_ACCESS_TOKEN."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "PLAID_CLIENT_ID": "client-id",
            "PLAID_SECRET": "token-value",
            "PLAID_ENV": "sandbox",
        },
        require_plaid_client=True,
    )

    assert cfg.plaid_client_id == "client-id"
    assert cfg.plaid_access_token is None


def test_load_config_item_id_defaults_to_default_item() -> None:
    """item_id falls back to DEFAULT_ITEM_ID when env var is absent."""
    cfg = load_config({"CLAW_PLAID_LEDGER_DB_PATH": "ledger.db"})

    assert cfg.item_id == DEFAULT_ITEM_ID


def test_load_config_item_id_reads_from_env_var() -> None:
    """item_id is loaded from CLAW_PLAID_LEDGER_ITEM_ID when set."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_PLAID_LEDGER_ITEM_ID": "my-bank-item",
        }
    )

    assert cfg.item_id == "my-bank-item"


def test_load_config_invalid_log_level_raises_config_error() -> None:
    """CLAW_LOG_LEVEL with an unrecognised value raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid CLAW_LOG_LEVEL"):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "CLAW_LOG_LEVEL": "INVALID",
            }
        )


def test_load_config_debug_log_level_accepted() -> None:
    """CLAW_LOG_LEVEL=DEBUG is a recognised level and loads without error."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_LOG_LEVEL": "DEBUG",
        }
    )

    assert cfg.log_level == "DEBUG"


def test_load_config_log_level_defaults_to_info() -> None:
    """log_level defaults to INFO when CLAW_LOG_LEVEL is not set."""
    cfg = load_config({"CLAW_PLAID_LEDGER_DB_PATH": "ledger.db"})

    assert cfg.log_level == "INFO"


def test_load_config_openclaw_defaults_when_vars_absent() -> None:
    """All four OpenClaw vars absent → defaults applied."""
    cfg = load_config({"CLAW_PLAID_LEDGER_DB_PATH": "ledger.db"})

    assert cfg.openclaw_hooks_url == "http://127.0.0.1:18789/hooks/agent"
    assert cfg.openclaw_hooks_token is None
    assert cfg.openclaw_hooks_agent == "Hestia"
    assert cfg.openclaw_hooks_wake_mode == "now"


def test_load_config_openclaw_reads_all_four_vars() -> None:
    """All four OpenClaw vars set → values are read correctly."""
    expected_auth = "test-hooks-auth-value"
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "OPENCLAW_HOOKS_URL": "http://example.com/hooks/agent",
            "OPENCLAW_HOOKS_TOKEN": expected_auth,
            "OPENCLAW_HOOKS_AGENT": "Hal9000",
            "OPENCLAW_HOOKS_WAKE_MODE": "later",
        }
    )

    assert cfg.openclaw_hooks_url == "http://example.com/hooks/agent"
    assert cfg.openclaw_hooks_token == expected_auth
    assert cfg.openclaw_hooks_agent == "Hal9000"
    assert cfg.openclaw_hooks_wake_mode == "later"


def test_load_config_openclaw_token_empty_string_stored_as_none() -> None:
    """OPENCLAW_HOOKS_TOKEN set to empty string is stored as None."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "OPENCLAW_HOOKS_TOKEN": "",
        }
    )

    assert cfg.openclaw_hooks_token is None
