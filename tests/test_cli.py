"""CLI smoke tests."""

from __future__ import annotations

import sys
from contextlib import redirect_stdout
from io import StringIO

from claw_plaid_ledger.cli import main


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
    assert "{doctor}" in output


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
