"""CLI smoke tests."""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

from claw_plaid_ledger.cli import main, serve
from claw_plaid_ledger.db import initialize_database, upsert_sync_state
from claw_plaid_ledger.items_config import (
    ItemConfig,
    ItemsConfigError,
    SuppressedAccountConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run_main(args: list[str]) -> tuple[int, str]:
    """Run CLI main with custom argv and return exit code and stdout text."""
    old_argv = sys.argv
    buffer = StringIO()

    try:
        sys.argv = ["ledger", *args]
        with redirect_stdout(buffer):
            try:
                main()
                exit_code = 0
            except SystemExit as exc:
                exit_code = 0 if exc.code is None else int(exc.code)
    finally:
        sys.argv = old_argv

    return exit_code, _ANSI_ESCAPE.sub("", buffer.getvalue())


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


INIT_DB_CONFIG_ERROR_EXIT_CODE = 2


def test_init_db_success(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """`init-db` creates a SQLite file and reports success."""
    db_path = tmp_path / "data" / "ledger.db"
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    exit_code, output = run_main(["init-db"])

    assert exit_code == 0
    assert db_path.exists()
    assert f"init-db: initialized database at {db_path}" in output


def test_init_db_missing_db_path() -> None:
    """`init-db` fails clearly when DB path is not configured."""
    original = os.environ.pop("CLAW_PLAID_LEDGER_DB_PATH", None)

    try:
        exit_code, output = run_main(["init-db"])
    finally:
        if original is not None:
            os.environ["CLAW_PLAID_LEDGER_DB_PATH"] = original

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "init-db: Missing required environment variable(s):" in output


def test_sync_missing_plaid_config(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`sync` fails clearly when Plaid env vars are missing."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    monkeypatch.delenv("PLAID_ENV", raising=False)
    monkeypatch.delenv("PLAID_ACCESS_TOKEN", raising=False)

    exit_code, output = run_main(["sync"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync: Missing required environment variable(s):" in output


def test_sync_success_calls_engine(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`sync` invokes adapter + sync engine and prints a concise summary."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    access_token = secrets.token_urlsafe(12)
    monkeypatch.setenv("PLAID_ACCESS_TOKEN", access_token)

    class DummyAdapter:
        pass

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        assert kwargs["access_token"] == access_token
        assert str(kwargs["db_path"]).endswith("ledger.db")
        assert isinstance(kwargs["adapter"], DummyAdapter)
        return SimpleNamespace(
            added=2,
            modified=1,
            removed=0,
            accounts=3,
            next_cursor="cursor-1",
        )

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync"])

    assert exit_code == 0
    assert "sync: accounts=3 added=2 modified=1 removed=0" in output


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


def test_sync_item_success_calls_engine_with_item_owner(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --item` uses items.toml id, token env var, and owner."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.delenv("PLAID_ACCESS_TOKEN", raising=False)
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    access_token_alice = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_alice, access_token_alice)

    class DummyAdapter:
        pass

    called: dict[str, object] = {}

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        called.update(kwargs)
        return SimpleNamespace(
            added=2,
            modified=1,
            removed=0,
            accounts=3,
            next_cursor="cursor-1",
        )

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            )
        ],
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync", "--item", "bank-alice"])

    assert exit_code == 0
    assert called["item_id"] == "bank-alice"
    assert called["owner"] == "alice"
    assert called["access_token"] == access_token_alice
    assert (
        "sync[bank-alice]: accounts=3 added=2 modified=1 removed=0" in output
    )


def test_sync_item_missing_id_exits_2(monkeypatch: MonkeyPatch) -> None:
    """`sync --item` exits 2 when the requested item id is missing."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["sync", "--item", "missing-id"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync: item 'missing-id' not found in items.toml" in output


def test_sync_item_missing_token_env_exits_2(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --item` exits 2 when the configured token env var is absent."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.delenv(env_var_alice, raising=False)
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            )
        ],
    )

    exit_code, output = run_main(["sync", "--item", "bank-alice"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert f"{env_var_alice} is not set" in output


def test_sync_all_success_runs_all_items(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --all` runs each configured item and exits zero on success."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    access_token_alice = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_alice, access_token_alice)
    env_var_bob = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setenv(env_var_bob, secrets.token_urlsafe(12))

    class DummyAdapter:
        pass

    calls: list[str] = []

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        calls.append(str(kwargs["item_id"]))
        return SimpleNamespace(
            added=1,
            modified=0,
            removed=0,
            accounts=1,
            next_cursor="cursor-1",
        )

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            ),
            ItemConfig(
                id="bank-bob",
                access_token_env=env_var_bob,
                owner="bob",
            ),
        ],
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync", "--all"])

    assert exit_code == 0
    assert calls == ["bank-alice", "bank-bob"]
    assert (
        "sync[bank-alice]: accounts=1 added=1 modified=0 removed=0" in output
    )
    assert "sync[bank-bob]: accounts=1 added=1 modified=0 removed=0" in output
    assert "sync --all: 2 items synced, 0 failed" in output


def test_sync_all_continues_when_one_item_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --all` continues after one item error and exits with code 1."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    access_token_alice = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_alice, access_token_alice)
    env_var_bob = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setenv(env_var_bob, secrets.token_urlsafe(12))

    class DummyAdapter:
        pass

    calls: list[str] = []

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        item_id = str(kwargs["item_id"])
        calls.append(item_id)
        if item_id == "bank-bob":
            message = "boom"
            raise RuntimeError(message)
        return SimpleNamespace(
            added=1,
            modified=0,
            removed=0,
            accounts=1,
            next_cursor="cursor-1",
        )

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            ),
            ItemConfig(
                id="bank-bob",
                access_token_env=env_var_bob,
                owner="bob",
            ),
        ],
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync", "--all"])

    assert exit_code == 1
    assert calls == ["bank-alice", "bank-bob"]
    assert (
        "sync[bank-alice]: accounts=1 added=1 modified=0 removed=0" in output
    )
    assert "sync[bank-bob]: ERROR boom" in output
    assert "sync --all: 1 items synced, 1 failed" in output


def test_sync_all_exits_2_when_items_empty(monkeypatch: MonkeyPatch) -> None:
    """`sync --all` exits 2 when items.toml has no items."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["sync", "--all"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync --all: no items found in items.toml" in output


def test_sync_item_and_all_are_mutually_exclusive() -> None:
    """`sync --item` and `--all` together exits with code 2."""
    exit_code, output = run_main(["sync", "--item", "foo", "--all"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync: --item and --all are mutually exclusive" in output


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


# ---------------------------------------------------------------------------
# ledger link tests
# ---------------------------------------------------------------------------

_LINK_CONFIG_ERROR_EXIT_CODE = 2


class _FakeServer:
    """Minimal stand-in for http.server.HTTPServer used in link tests."""

    def shutdown(self) -> None:
        """No-op shutdown."""


def test_link_missing_plaid_config_exits_2(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` exits 2 when required Plaid env vars are absent."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    monkeypatch.delenv("PLAID_ENV", raising=False)

    exit_code, output = run_main(["link"])

    assert exit_code == _LINK_CONFIG_ERROR_EXIT_CODE
    assert "link: Missing required environment variable(s):" in output


def test_link_create_token_error_exits_1(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` exits 1 when create_link_token raises."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    class _ErrorAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            _products: list[str],
            _country_codes: list[str],
        ) -> str:
            msg = "Plaid permanent API error (HTTP 400): ..."
            raise RuntimeError(msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _ErrorAdapter(),
    )

    exit_code, output = run_main(["link"])

    assert exit_code == 1
    assert "link: failed to create link token" in output


def test_link_success_prints_access_token(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` end-to-end: creates token, receives callback, exchanges."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    # Pre-set done_event so CLI `done_event.wait()` returns immediately.
    fake_done = threading.Event()
    fake_result: list[str] = ["public-sandbox-test-token"]
    fake_done.set()

    class _SuccessAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            _products: list[str],
            _country_codes: list[str],
        ) -> str:
            return "link-sandbox-fake"

        def exchange_public_token(self, public_token: str) -> tuple[str, str]:
            assert public_token == "public-sandbox-test-token"  # noqa: S105
            return "access-sandbox-fake-token", "item-abc"

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _SuccessAdapter(),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.start_link_server",
        lambda _tok: (_FakeServer(), fake_done, fake_result),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.webbrowser.open", lambda _url: None
    )

    exit_code, output = run_main(["link"])

    assert exit_code == 0
    assert "access-sandbox-fake-token" in output
    assert "item-abc" in output
    assert "items.toml" in output


def test_link_default_product_is_transactions(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` with no --products flag defaults to transactions."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    captured_products: list[list[str]] = []
    fake_done = threading.Event()
    fake_result: list[str] = ["public-tok"]
    fake_done.set()

    class _CaptureAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            products: list[str],
            _country_codes: list[str],
        ) -> str:
            captured_products.append(products)
            return "link-sandbox-fake"

        def exchange_public_token(self, _public_token: str) -> tuple[str, str]:
            return "access-tok", "item-id"

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _CaptureAdapter(),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.start_link_server",
        lambda _tok: (_FakeServer(), fake_done, fake_result),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.webbrowser.open", lambda _url: None
    )

    exit_code, _output = run_main(["link"])

    assert exit_code == 0
    assert captured_products == [["transactions"]]


def test_link_keyboard_interrupt_exits_1(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` exits 1 with an informative message on KeyboardInterrupt."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    class _InterruptEvent:
        """Raises KeyboardInterrupt when wait() is called."""

        def wait(self) -> None:
            """Simulate Ctrl-C from the operator."""
            raise KeyboardInterrupt

    class _InterruptAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            _products: list[str],
            _country_codes: list[str],
        ) -> str:
            return "link-sandbox-fake"

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _InterruptAdapter(),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.start_link_server",
        lambda _tok: (_FakeServer(), _InterruptEvent(), []),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.webbrowser.open", lambda _url: None
    )

    exit_code, output = run_main(["link"])

    assert exit_code == 1
    assert "interrupted" in output


# ---------------------------------------------------------------------------
# ledger items tests
# ---------------------------------------------------------------------------


def test_items_no_items_configured(monkeypatch: MonkeyPatch) -> None:
    """`items` exits 0 and reports no items when items.toml is absent."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "no items configured" in output


def test_items_parse_error_exits_1(monkeypatch: MonkeyPatch) -> None:
    """`items` exits 1 and reports the error when items.toml is invalid."""

    def raise_items_error() -> list[ItemConfig]:
        message = "items[0] missing required field 'id'"
        raise ItemsConfigError(message)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config", raise_items_error
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 1
    assert "items: parse error:" in output
    assert "items[0] missing required field 'id'" in output


def test_items_mixed_tokens_set_and_missing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`items` shows SET/MISSING per token and a correct summary line."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    bob_env = "PLAID_ACCESS_TOKEN_CARD_BOB"
    monkeypatch.setenv(alice_env, "access-sandbox-token-alice")
    monkeypatch.delenv(bob_env, raising=False)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
            ),
            ItemConfig(
                id="card-bob",
                access_token_env=bob_env,
                owner="bob",
            ),
        ],
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "items: bank-alice owner=alice token=SET" in output
    assert "items: card-bob owner=bob token=MISSING" in output
    assert "items: 1/2 items healthy, 1 need attention" in output


def test_items_shows_account_and_sync_counts(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`items` shows correct account and sync-state counts from the DB."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, item_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "acct-1",
                "Checking",
                "bank-alice",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, item_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "acct-2",
                "Savings",
                "bank-alice",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        upsert_sync_state(
            conn,
            item_id="bank-alice",
            cursor="cursor-1",
            owner="alice",
            last_synced_at="2026-03-10T14:22:00+00:00",
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.setenv(alice_env, "access-sandbox-token-alice")

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
            ),
        ],
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "accounts=2" in output
    assert "last_synced=2026-03-10T14:22:00+00:00" in output
    assert "items: 1/1 items healthy, 0 need attention" in output


def test_items_no_owner_shows_none_placeholder(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`items` shows (none) when owner is not set for an item."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    card_env = "PLAID_ACCESS_TOKEN_CARD_SHARED"
    monkeypatch.setenv(card_env, "access-token-shared")

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="card-shared",
                access_token_env=card_env,
                owner=None,
            ),
        ],
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "owner=(none)" in output


# ---------------------------------------------------------------------------
# Task 2: apply-precedence command
# ---------------------------------------------------------------------------


def test_apply_precedence_no_suppressions_exits_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    """`apply-precedence` exits 0 when no aliases are configured."""
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
            )
        ],
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "no suppressions configured" in output


def test_apply_precedence_empty_items_config_exits_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    """`apply-precedence` exits 0 when items.toml has no items."""
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        list,
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "no suppressions configured" in output


def test_apply_precedence_updates_known_account(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`apply-precedence` updates an account that exists in the DB."""
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, type, created_at, updated_at) "
            "VALUES ('acct-suppressed', 'Test', 'credit', "
            "'2024-01-01', '2024-01-01')"
        )

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-suppressed",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "loaded 1 alias(es)" in output
    assert "updated 1 account(s)" in output
    assert "done" in output


def test_apply_precedence_skips_account_not_in_db(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`apply-precedence` reports skipped aliases for unsynced accounts."""
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-not-synced",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "1 alias(es) skipped" in output
    assert "sync first" in output


def test_apply_precedence_config_error_exits_one(
    monkeypatch: MonkeyPatch,
) -> None:
    """`apply-precedence` exits 1 on ItemsConfigError."""
    error_msg = "bad config"

    def _raise_config_error() -> list[ItemConfig]:
        raise ItemsConfigError(error_msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        _raise_config_error,
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 1
    assert "config error" in output


# ---------------------------------------------------------------------------
# Task 4: overlaps command
# ---------------------------------------------------------------------------


def test_overlaps_no_suppressions_exits_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    """`overlaps` exits 0 with a no-config message when empty."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "overlaps: no suppressions configured" in output


def test_overlaps_reports_in_db_status(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` shows IN DB when suppression matches DB state."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, mask, type, item_id, "
            "canonical_account_id, "
            "created_at, updated_at) "
            "VALUES ('acct-suppressed', 'Premium Rewards', '4321', "
            "'credit', 'bank-alice', 'acct-canonical', '2024-01-01', "
            "'2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-suppressed",
                        canonical_account_id="acct-canonical",
                        canonical_from_item="card-bob",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "Configured suppressions" in output
    assert "[IN DB]" in output
    assert "card-bob" in output
    assert "0 configured suppression active" not in output


def test_overlaps_reports_not_yet_synced_status(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` shows NOT YET SYNCED for unknown suppressed account."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-not-synced",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "[NOT YET SYNCED — run sync first]" in output
    assert "0 configured suppression active" in output
    assert "1 pending sync" in output


def test_overlaps_reports_mismatch_status(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` shows MISMATCH when DB canonical differs from config."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, type, item_id, canonical_account_id, "
            "created_at, updated_at) "
            "VALUES ('acct-suppressed', 'Premium Rewards', 'credit', "
            "'bank-alice', 'acct-old-canonical', '2024-01-01', "
            "'2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-suppressed",
                        canonical_account_id="acct-new-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "[MISMATCH]" in output
    assert "0 configured suppression active" in output


def test_overlaps_detects_potential_unconfirmed_overlaps(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` flags same name/mask/type accounts across items."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, mask, type, item_id, "
            "canonical_account_id, "
            "created_at, updated_at) VALUES "
            "('acct-shared-a', 'Premium Rewards', '4321', 'credit', "
            "'bank-alice', 'acct-canonical', '2024-01-01', '2024-01-01'), "
            "('acct-shared-b', 'Premium Rewards', '4321', 'credit', "
            "'card-bob', NULL, '2024-01-01', '2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-shared-a",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "Potential unconfirmed overlaps" in output
    assert '"Premium Rewards"  mask=4321  type=credit' in output
    assert "items: bank-alice, card-bob" in output
    assert "1 potential overlap flagged" in output


def test_overlaps_no_unconfirmed_overlaps_reports_none_detected(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` prints none detected when no overlap candidates exist."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, type, item_id, canonical_account_id, "
            "created_at, updated_at) "
            "VALUES ('acct-solo', 'Everyday Checking', 'depository', "
            "'bank-alice', 'acct-canonical', '2024-01-01', '2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-solo",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "none detected" in output
    assert "0 potential overlap flagged" in output
