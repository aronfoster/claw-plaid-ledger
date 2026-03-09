"""CLI smoke tests."""

from __future__ import annotations

import os
import re
import secrets
import sys
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING

from claw_plaid_ledger.cli import main
from claw_plaid_ledger.db import initialize_database

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
