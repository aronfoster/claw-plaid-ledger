"""CLI smoke tests."""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import sys
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

from claw_plaid_ledger.cli import main, serve
from claw_plaid_ledger.db import initialize_database, upsert_sync_state
from claw_plaid_ledger.items_config import ItemConfig, ItemsConfigError

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
