"""CLI smoke tests."""

from __future__ import annotations

import sys
from contextlib import redirect_stdout
from io import StringIO

from claw_plaid_ledger.cli import main


def run_main(args: list[str]) -> str:
    """Run CLI main with custom argv and return stdout text."""
    old_argv = sys.argv
    buffer = StringIO()
    try:
        sys.argv = ["ledger", *args]
        with redirect_stdout(buffer):
            main()
    finally:
        sys.argv = old_argv
    return buffer.getvalue()


def test_doctor_default() -> None:
    """`doctor` command returns the baseline setup status."""
    output = run_main(["doctor"])

    assert "doctor: basic checks passed" in output


def test_doctor_verbose() -> None:
    """`doctor --verbose` returns the verbose placeholder status."""
    output = run_main(["doctor", "--verbose"])

    assert "doctor: verbose diagnostics not implemented yet" in output
