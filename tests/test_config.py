"""Configuration loading tests."""

from __future__ import annotations

import ipaddress
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
    """CLAW_LOG_LEVEL with an unrecognized value raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid CLAW_LOG_LEVEL"):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "CLAW_LOG_LEVEL": "INVALID",
            }
        )


def test_load_config_debug_log_level_accepted() -> None:
    """CLAW_LOG_LEVEL=DEBUG is a recognized level and loads without error."""
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


# ---------------------------------------------------------------------------
# Scheduled sync configuration
# ---------------------------------------------------------------------------

_DEFAULT_FALLBACK_HOURS = 24
_CUSTOM_FALLBACK_HOURS = 48
_MINIMUM_FALLBACK_HOURS = 1


def test_scheduled_sync_disabled_by_default() -> None:
    """CLAW_SCHEDULED_SYNC_ENABLED defaults to False when absent."""
    cfg = load_config({"CLAW_PLAID_LEDGER_DB_PATH": "ledger.db"})

    assert cfg.scheduled_sync_enabled is False


def test_scheduled_sync_enabled_when_set_true() -> None:
    """CLAW_SCHEDULED_SYNC_ENABLED=true enables the feature."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_SCHEDULED_SYNC_ENABLED": "true",
        }
    )

    assert cfg.scheduled_sync_enabled is True


def test_scheduled_sync_fallback_hours_defaults_to_24() -> None:
    """CLAW_SCHEDULED_SYNC_FALLBACK_HOURS defaults to 24 when absent."""
    cfg = load_config({"CLAW_PLAID_LEDGER_DB_PATH": "ledger.db"})

    assert cfg.scheduled_sync_fallback_hours == _DEFAULT_FALLBACK_HOURS


def test_scheduled_sync_fallback_hours_read_from_env_var() -> None:
    """CLAW_SCHEDULED_SYNC_FALLBACK_HOURS is parsed from the environment."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_SCHEDULED_SYNC_FALLBACK_HOURS": "48",
        }
    )

    assert cfg.scheduled_sync_fallback_hours == _CUSTOM_FALLBACK_HOURS


def test_scheduled_sync_fallback_hours_zero_rejected() -> None:
    """CLAW_SCHEDULED_SYNC_FALLBACK_HOURS=0 raises a startup ConfigError."""
    with pytest.raises(
        ConfigError, match="CLAW_SCHEDULED_SYNC_FALLBACK_HOURS"
    ):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "CLAW_SCHEDULED_SYNC_FALLBACK_HOURS": "0",
            }
        )


def test_scheduled_sync_fallback_hours_negative_rejected() -> None:
    """Negative CLAW_SCHEDULED_SYNC_FALLBACK_HOURS raises ConfigError."""
    with pytest.raises(
        ConfigError, match="CLAW_SCHEDULED_SYNC_FALLBACK_HOURS"
    ):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "CLAW_SCHEDULED_SYNC_FALLBACK_HOURS": "-5",
            }
        )


def test_scheduled_sync_fallback_hours_non_integer_rejected() -> None:
    """Non-integer CLAW_SCHEDULED_SYNC_FALLBACK_HOURS raises ConfigError."""
    with pytest.raises(
        ConfigError, match="CLAW_SCHEDULED_SYNC_FALLBACK_HOURS"
    ):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "CLAW_SCHEDULED_SYNC_FALLBACK_HOURS": "not-a-number",
            }
        )


def test_scheduled_sync_fallback_hours_minimum_one_accepted() -> None:
    """CLAW_SCHEDULED_SYNC_FALLBACK_HOURS=1 is the minimum valid value."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_SCHEDULED_SYNC_FALLBACK_HOURS": "1",
        }
    )

    assert cfg.scheduled_sync_fallback_hours == _MINIMUM_FALLBACK_HOURS


# ---------------------------------------------------------------------------
# Webhook IP allowlist configuration
# ---------------------------------------------------------------------------


def test_webhook_allowed_ips_unset_defaults_to_empty() -> None:
    """CLAW_WEBHOOK_ALLOWED_IPS absent → empty list (no filtering)."""
    cfg = load_config({"CLAW_PLAID_LEDGER_DB_PATH": "ledger.db"})

    assert cfg.webhook_allowed_ips == []


def test_webhook_allowed_ips_empty_string_defaults_to_empty() -> None:
    """CLAW_WEBHOOK_ALLOWED_IPS set to empty string → empty list."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_WEBHOOK_ALLOWED_IPS": "",
        }
    )

    assert cfg.webhook_allowed_ips == []


def test_webhook_allowed_ips_single_cidr_parsed() -> None:
    """A single valid CIDR is parsed into a network object."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_WEBHOOK_ALLOWED_IPS": "52.21.0.0/16",
        }
    )

    assert len(cfg.webhook_allowed_ips) == 1
    assert cfg.webhook_allowed_ips[0] == ipaddress.ip_network("52.21.0.0/16")


_TWO_CIDRS = 2


def test_webhook_allowed_ips_multiple_cidrs_parsed() -> None:
    """Multiple comma-separated CIDRs are all parsed."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_WEBHOOK_ALLOWED_IPS": "52.21.0.0/16,3.211.0.0/16",
        }
    )

    assert len(cfg.webhook_allowed_ips) == _TWO_CIDRS
    assert ipaddress.ip_network("52.21.0.0/16") in cfg.webhook_allowed_ips
    assert ipaddress.ip_network("3.211.0.0/16") in cfg.webhook_allowed_ips


def test_webhook_allowed_ips_invalid_cidr_raises_config_error() -> None:
    """Invalid CIDR in CLAW_WEBHOOK_ALLOWED_IPS raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid CIDR"):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "CLAW_WEBHOOK_ALLOWED_IPS": "not-a-cidr",
            }
        )


def test_webhook_allowed_ips_ipv6_cidr_parsed() -> None:
    """An IPv6 CIDR is parsed correctly."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_WEBHOOK_ALLOWED_IPS": "2001:db8::/32",
        }
    )

    assert len(cfg.webhook_allowed_ips) == 1
    assert cfg.webhook_allowed_ips[0] == ipaddress.ip_network("2001:db8::/32")


def test_trusted_proxies_defaults_to_loopback() -> None:
    """CLAW_TRUSTED_PROXIES absent → [IPv4Address('127.0.0.1')]."""
    cfg = load_config({"CLAW_PLAID_LEDGER_DB_PATH": "ledger.db"})

    assert cfg.trusted_proxies == [ipaddress.IPv4Address("127.0.0.1")]


def test_trusted_proxies_custom_ip_parsed() -> None:
    """A custom proxy IP is parsed into an address object."""
    cfg = load_config(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
            "CLAW_TRUSTED_PROXIES": "10.0.0.1",
        }
    )

    assert cfg.trusted_proxies == [ipaddress.IPv4Address("10.0.0.1")]


def test_trusted_proxies_invalid_ip_raises_config_error() -> None:
    """Invalid IP in CLAW_TRUSTED_PROXIES raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid IP address"):
        load_config(
            {
                "CLAW_PLAID_LEDGER_DB_PATH": "ledger.db",
                "CLAW_TRUSTED_PROXIES": "not-an-ip",
            }
        )
