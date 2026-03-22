"""CLI doctor and serve-startup tests."""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

from claw_plaid_ledger.cli import serve
from claw_plaid_ledger.db import initialize_database, upsert_sync_state
from claw_plaid_ledger.items_config import ItemConfig, ItemsConfigError
from tests.helpers import run_main

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def test_help() -> None:
    """`--help` shows command usage information."""
    exit_code, output = run_main(["--help"])

    assert exit_code == 0
    assert "Usage: ledger" in output
    assert "doctor" in output
    assert "init-db" in output
    assert "sync" in output


def test_doctor_missing_config() -> None:
    """`doctor` exits non-zero when required env var is missing."""
    original = os.environ.pop("CLAW_PLAID_LEDGER_DB_PATH", None)

    try:
        exit_code, output = run_main(["doctor"])
    finally:
        if original is not None:
            os.environ["CLAW_PLAID_LEDGER_DB_PATH"] = original

    assert exit_code != 0
    assert "doctor: env [FAIL]" in output
    assert "CLAW_PLAID_LEDGER_DB_PATH" in output


def test_doctor_missing_db_file(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` exits non-zero when the DB file does not exist."""
    db_path = tmp_path / "missing.db"
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    exit_code, output = run_main(["doctor"])

    assert exit_code != 0
    assert "doctor: db [FAIL]" in output


def test_doctor_healthy(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """`doctor` exits zero and reports row counts when healthy."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "doctor: env [OK]" in output
    assert "doctor: db [OK]" in output
    assert "doctor: schema [OK]" in output
    assert "doctor: sync_state rows=0 last_synced_at=never" in output
    assert "doctor: accounts rows=0" in output
    assert "doctor: transactions rows=0" in output
    assert "doctor: all checks passed" in output


def test_doctor_verbose_redacts_secrets(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor --verbose` shows config values with secrets redacted."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id-value")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_SECRET", "supersecretvalue1234")
    monkeypatch.setenv("PLAID_ACCESS_TOKEN", "access-token-abcdefgh")

    exit_code, output = run_main(["doctor", "--verbose"])

    assert exit_code == 0
    assert "doctor: all checks passed" in output
    # Non-secret values shown in full
    assert "PLAID_CLIENT_ID=client-id-value" in output
    assert "PLAID_ENV=sandbox" in output
    # Secrets redacted to last 4 chars
    assert "PLAID_SECRET=****1234" in output
    assert "PLAID_ACCESS_TOKEN=****efgh" in output
    # Full secret values must not appear in output
    assert "supersecretvalue1234" not in output
    assert "access-token-abcdefgh" not in output


def test_doctor_reports_api_secret_set(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` reports [OK] when CLAW_API_SECRET is set."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setenv("CLAW_API_SECRET", "some-secret-value")

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "CLAW_API_SECRET [OK]" in output


def test_doctor_reports_api_secret_unset(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` reports [FAIL] when CLAW_API_SECRET is not set."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.delenv("CLAW_API_SECRET", raising=False)

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "CLAW_API_SECRET [FAIL]" in output


def test_doctor_openclaw_notification_warn_when_token_unset(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` reports [WARN] and exits 0 when token is not set."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.delenv("OPENCLAW_HOOKS_TOKEN", raising=False)

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "openclaw notification [WARN]" in output


def test_doctor_openclaw_notification_ok_when_token_set(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` reports [OK] with url and agent when token is set."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setenv("OPENCLAW_HOOKS_TOKEN", "my-token")

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "openclaw notification [OK]" in output
    assert "url=http://127.0.0.1:18789/hooks/agent" in output
    assert "agent=Hestia" in output
    assert "my-token" not in output


def test_doctor_openclaw_notification_shows_custom_agent(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` shows the custom agent name when OPENCLAW_HOOKS_AGENT set."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setenv("OPENCLAW_HOOKS_TOKEN", "my-token")
    monkeypatch.setenv("OPENCLAW_HOOKS_AGENT", "Hal9000")

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "agent=Hal9000" in output


def test_doctor_scheduled_sync_disabled_by_default(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` reports scheduled sync DISABLED when env var is absent."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.delenv("CLAW_SCHEDULED_SYNC_ENABLED", raising=False)

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "scheduled-sync: DISABLED" in output
    assert "CLAW_SCHEDULED_SYNC_ENABLED=true" in output


def test_doctor_scheduled_sync_enabled_shows_config(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` reports scheduled sync ENABLED with fallback window."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setenv("CLAW_SCHEDULED_SYNC_ENABLED", "true")
    monkeypatch.setenv("CLAW_SCHEDULED_SYNC_FALLBACK_HOURS", "12")

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "scheduled-sync: ENABLED" in output
    assert "12h" in output
    assert "60min" in output


def test_doctor_scheduled_sync_does_not_cause_nonzero_exit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` exits zero regardless of scheduled sync configuration."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setenv("CLAW_SCHEDULED_SYNC_ENABLED", "false")

    exit_code, _ = run_main(["doctor"])

    assert exit_code == 0


def test_doctor_items_toml_absent_reports_single_item_mode(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` reports single-item mode when items.toml is absent."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "doctor: items.toml not found — single-item mode" in output


def test_doctor_reports_per_item_synced_state(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` prints one per-item status line for synced items."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_sync_state(
            conn,
            item_id="bank-alice",
            cursor="cursor-1",
            owner="alice",
            last_synced_at="2024-01-15T08:30:00+00:00",
        )
        upsert_sync_state(
            conn,
            item_id="bank-bob",
            cursor="cursor-2",
            owner="bob",
            last_synced_at="2024-01-16T08:30:00+00:00",
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    alice_env_name = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    bob_env_name = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env_name,
                owner="alice",
            ),
            ItemConfig(
                id="bank-bob",
                access_token_env=bob_env_name,
                owner="bob",
            ),
        ],
    )

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert (
        "doctor: item bank-alice "
        "owner=alice "
        "last_synced_at=2024-01-15T08:30:00+00:00" in output
    )
    assert (
        "doctor: item bank-bob "
        "owner=bob "
        "last_synced_at=2024-01-16T08:30:00+00:00" in output
    )


def test_doctor_reports_unsynced_item_as_never(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` prints last_synced_at=never for unsynced configured items."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    bob_env_name = "PLAID_ACCESS_TOKEN_CARD_BOB"
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="card-bob",
                access_token_env=bob_env_name,
                owner="bob",
            )
        ],
    )

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "doctor: item card-bob owner=bob last_synced_at=never" in output


def test_doctor_reports_orphaned_sync_state_rows(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` marks sync_state rows missing from items.toml as orphans."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_sync_state(
            conn,
            item_id="default-item",
            cursor="cursor-legacy",
            owner=None,
            last_synced_at="2024-01-10T06:00:00+00:00",
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    alice_env_name = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env_name,
                owner="alice",
            )
        ],
    )

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert (
        "doctor: item default-item "
        "owner=None "
        "last_synced_at=2024-01-10T06:00:00+00:00 [not in items.toml]"
        in output
    )


def test_doctor_items_toml_parse_error_is_warn_only(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`doctor` warns and still exits 0 when items.toml cannot be parsed."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    def raise_items_error() -> list[ItemConfig]:
        message = "items[0] missing required field 'id'"
        raise ItemsConfigError(message)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config", raise_items_error
    )

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert (
        "doctor: items.toml [WARN] parse error: "
        "items[0] missing required field 'id'"
    ) in output


def test_serve_refuses_without_api_secret(monkeypatch: MonkeyPatch) -> None:
    """`serve` exits non-zero when CLAW_API_SECRET is not set."""
    monkeypatch.delenv("CLAW_API_SECRET", raising=False)

    exit_code, output = run_main(["serve"])

    assert exit_code != 0
    assert "CLAW_API_SECRET" in output


def test_serve_refuses_invalid_log_level(monkeypatch: MonkeyPatch) -> None:
    """`serve` exits non-zero when CLAW_LOG_LEVEL is invalid."""
    monkeypatch.setenv("CLAW_API_SECRET", "some-secret-value")
    monkeypatch.setenv("CLAW_LOG_LEVEL", "INVALID")

    exit_code, output = run_main(["serve"])

    assert exit_code != 0
    assert "CLAW_LOG_LEVEL" in output


def test_serve_logs_startup_info(monkeypatch: MonkeyPatch) -> None:
    """`serve` emits an INFO log containing host and port before starting."""
    monkeypatch.setenv("CLAW_API_SECRET", "some-secret-value")
    monkeypatch.setenv("CLAW_SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAW_SERVER_PORT", "9999")
    monkeypatch.delenv("CLAW_LOG_LEVEL", raising=False)

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)

    try:
        with patch("claw_plaid_ledger.cli.uvicorn.run"):
            serve()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    messages = [r.getMessage() for r in records if r.levelno == logging.INFO]
    assert any("127.0.0.1" in m and "9999" in m for m in messages), (
        f"Expected INFO with host and port; got: {messages}"
    )


# ---------------------------------------------------------------------------
# doctor --production-preflight tests (Task 2 / Task 4)
# ---------------------------------------------------------------------------


def test_doctor_production_preflight_success(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`doctor --production-preflight` exits 0 with a valid production env."""
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAID_SECRET", "plaid-secret")
    monkeypatch.setenv("PLAID_ENV", "production")
    monkeypatch.setenv("CLAW_API_SECRET", "api-secret")
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.preflight.load_items_config",
        lambda _path: [],
    )

    exit_code, output = run_main(["doctor", "--production-preflight"])

    assert exit_code == 0
    assert "preflight: all required checks passed" in output


def test_doctor_production_preflight_missing_plaid_client_id_exits_nonzero(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Preflight exits non-zero when PLAID_CLIENT_ID is missing."""
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.setenv("PLAID_SECRET", "plaid-secret")
    monkeypatch.setenv("PLAID_ENV", "production")
    monkeypatch.setenv("CLAW_API_SECRET", "api-secret")
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.preflight.load_items_config",
        lambda _path: [],
    )

    exit_code, output = run_main(["doctor", "--production-preflight"])

    assert exit_code != 0
    assert "PLAID_CLIENT_ID" in output
    assert "[FAIL]" in output


def test_doctor_production_preflight_missing_api_secret_exits_nonzero(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Preflight exits non-zero when CLAW_API_SECRET is missing."""
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAID_SECRET", "plaid-secret")
    monkeypatch.setenv("PLAID_ENV", "production")
    monkeypatch.delenv("CLAW_API_SECRET", raising=False)
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.preflight.load_items_config",
        lambda _path: [],
    )

    exit_code, output = run_main(["doctor", "--production-preflight"])

    assert exit_code != 0
    assert "CLAW_API_SECRET" in output
    assert "[FAIL]" in output


def test_doctor_production_preflight_sandbox_warning_exits_zero(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Preflight exits 0 when PLAID_ENV=sandbox (warning, not a hard fail)."""
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAID_SECRET", "plaid-secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("CLAW_API_SECRET", "api-secret")
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.preflight.load_items_config",
        lambda _path: [],
    )

    exit_code, output = run_main(["doctor", "--production-preflight"])

    assert exit_code == 0
    assert "[WARN]" in output
    assert "sandbox" in output.lower()
    assert "preflight: all required checks passed" in output


def test_doctor_production_preflight_items_toml_error_exits_nonzero(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Preflight exits non-zero when items.toml cannot be parsed."""
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAID_SECRET", "plaid-secret")
    monkeypatch.setenv("PLAID_ENV", "production")
    monkeypatch.setenv("CLAW_API_SECRET", "api-secret")
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )

    def raise_items_error(_path: object) -> list[ItemConfig]:
        message = "items[0] missing required field 'id'"
        raise ItemsConfigError(message)

    monkeypatch.setattr(
        "claw_plaid_ledger.preflight.load_items_config",
        raise_items_error,
    )

    exit_code, output = run_main(["doctor", "--production-preflight"])

    assert exit_code != 0
    assert "items.toml" in output
    assert "[FAIL]" in output


def test_doctor_production_preflight_missing_token_env_exits_nonzero(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Preflight exits non-zero when items.toml token env var is absent."""
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAID_SECRET", "plaid-secret")
    monkeypatch.setenv("PLAID_ENV", "production")
    monkeypatch.setenv("CLAW_API_SECRET", "api-secret")
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.delenv("PLAID_TOKEN_BANK", raising=False)
    bank_cfg_env = "PLAID_TOKEN_BANK"
    monkeypatch.setattr(
        "claw_plaid_ledger.preflight.load_items_config",
        lambda _path: [ItemConfig(id="bank", access_token_env=bank_cfg_env)],
    )

    exit_code, output = run_main(["doctor", "--production-preflight"])

    assert exit_code != 0
    assert "PLAID_TOKEN_BANK" in output
    assert "[FAIL]" in output


def test_doctor_without_preflight_flag_omits_preflight_output(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Legacy `doctor` invocation exits 0 and shows no preflight output."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "doctor: all checks passed" in output
    assert "preflight:" not in output
