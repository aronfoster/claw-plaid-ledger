"""Small local subset of the Typer API used by this project."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class _OptionConfig:
    """Configuration metadata used for option defaults."""

    default: int
    flags: tuple[str, ...]
    count: bool


def option(default: int, *flags: str, count: bool = False) -> _OptionConfig:
    """Declare a CLI option on a command function."""
    return _OptionConfig(default=default, flags=flags, count=count)


class Typer:
    """Tiny command-oriented CLI app compatible with this project."""

    def __init__(self, *, help_text: str = "") -> None:
        """Initialize a command container."""
        self._help = help_text
        self._commands: dict[str, Callable[..., None]] = {}

    def command(self) -> Callable[[Callable[..., None]], Callable[..., None]]:
        """Register a command function."""

        def decorator(func: Callable[..., None]) -> Callable[..., None]:
            self._commands[func.__name__.replace("_", "-")] = func
            return func

        return decorator

    def __call__(self) -> None:
        """Parse arguments and dispatch to a command."""
        parser = argparse.ArgumentParser(prog="ledger", description=self._help)
        subparsers = parser.add_subparsers(dest="command", required=True)

        for name, func in self._commands.items():
            subparser = subparsers.add_parser(
                name, help=(func.__doc__ or "").strip()
            )
            defaults = func.__defaults__ or ()
            arg_names = func.__code__.co_varnames[: func.__code__.co_argcount]
            option_names = arg_names[-len(defaults) :] if defaults else ()

            for option_name, default in zip(
                option_names, defaults, strict=False
            ):
                if isinstance(default, _OptionConfig) and default.count:
                    subparser.add_argument(
                        *default.flags,
                        dest=option_name,
                        action="count",
                        default=default.default,
                    )

            subparser.set_defaults(handler=func)

        parsed = parser.parse_args()
        kwargs = {
            key: value
            for key, value in vars(parsed).items()
            if key not in {"command", "handler"}
        }
        parsed.handler(**kwargs)


def echo(message: str) -> None:
    """Write output with a newline."""
    sys.stdout.write(f"{message}\n")
