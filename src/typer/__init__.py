"""Small local subset of Typer used by this project."""

from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class _Option:
    """Option metadata for command parameters."""

    default: object
    flags: tuple[str, ...]
    count: bool


def Option(default: object, *flags: str, count: bool = False) -> _Option:  # noqa: N802
    """Declare a command option."""
    return _Option(default=default, flags=flags, count=count)


class Typer:
    """Minimal command app compatible with current project needs."""

    def __init__(self, *, help_text: str = "") -> None:
        """Initialize the app with top-level help text."""
        self._help = help_text
        self._commands: dict[str, Callable[..., None]] = {}

    def command(
        self,
    ) -> Callable[[Callable[..., None]], Callable[..., None]]:
        """Register a command function."""

        def decorator(func: Callable[..., None]) -> Callable[..., None]:
            self._commands[func.__name__.replace("_", "-")] = func
            return func

        return decorator

    def __call__(self) -> None:
        """Dispatch a CLI command from sys.argv."""
        self._run(sys.argv[1:])

    def _run(self, args: list[str]) -> None:
        if not args or args[0] in {"-h", "--help"}:
            self._print_help()
            return

        command_name = args[0]
        command = self._commands.get(command_name)
        if command is None:
            msg = f"Unknown command: {command_name}"
            raise SystemExit(msg)

        kwargs = self._parse_command_args(command, args[1:])
        command(**kwargs)

    def _print_help(self) -> None:
        echo(self._help)
        if self._commands:
            echo("\nCommands:")
            for name in sorted(self._commands):
                echo(f"  {name}")

    def _parse_command_args(
        self,
        command: Callable[..., None],
        args: list[str],
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        signature = inspect.signature(command)
        for param in signature.parameters.values():
            default = param.default
            if isinstance(default, _Option):
                if default.count:
                    count_value = 0
                    for arg in args:
                        if arg in default.flags:
                            count_value += 1
                    kwargs[param.name] = count_value
                    continue

                value = default.default
                for idx, arg in enumerate(args):
                    if arg in default.flags and idx + 1 < len(args):
                        value = args[idx + 1]
                kwargs[param.name] = value
                continue

            kwargs[param.name] = default

        return kwargs


def echo(message: str) -> None:
    """Print a CLI message to stdout."""
    print(message)  # noqa: T201
