"""Shared test helper functions (not fixtures)."""

from __future__ import annotations

import re
import sys
from contextlib import redirect_stdout
from io import StringIO

from claw_plaid_ledger.cli import main

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
