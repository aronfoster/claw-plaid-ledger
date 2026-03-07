"""Testing helpers for the local Typer subset."""

from __future__ import annotations

import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typer import Typer


@dataclass(frozen=True)
class Result:
    """CLI invocation result."""

    exit_code: int
    stdout: str


class CliRunner:
    """Minimal CLI runner that captures stdout."""

    def invoke(self, app: Typer, args: list[str]) -> Result:
        """Invoke the app with args and capture output."""
        old_argv = sys.argv
        buffer = StringIO()
        exit_code = 0
        try:
            sys.argv = ["ledger", *args]
            with redirect_stdout(buffer):
                app()
        except SystemExit as exc:
            exit_code = int(exc.code) if isinstance(exc.code, int) else 1
        finally:
            sys.argv = old_argv

        return Result(exit_code=exit_code, stdout=buffer.getvalue())
