"""CLI smoke tests."""

from __future__ import annotations

import os
import sys
from contextlib import redirect_stdout
from io import StringIO
from typing import TYPE_CHECKING

from claw_plaid_ledger.cli import main

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


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

    return exit_code, buffer.getvalue()


def test_help() -> None:
    """`--help` shows command usage information."""
    exit_code, output = run_main(["--help"])

    assert exit_code == 0
    assert "usage: ledger" in output
    assert "doctor" in output
    assert "init-db" in output


def test_doctor_default() -> None:
    """`doctor` command returns the baseline setup status."""
    exit_code, output = run_main(["doctor"])

    assert exit_code == 0
    assert "doctor: basic checks passed" in output


def test_doctor_verbose() -> None:
    """`doctor --verbose` returns the verbose placeholder status."""
    exit_code, output = run_main(["doctor", "--verbose"])

    assert exit_code == 0
    assert "doctor: verbose diagnostics not implemented yet" in output


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
