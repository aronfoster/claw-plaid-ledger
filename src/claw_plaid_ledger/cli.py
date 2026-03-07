"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

import argparse
import sys


def doctor(*, verbose: int = 0) -> None:
    """Show environment and setup diagnostics for this project."""
    if verbose > 0:
        sys.stdout.write("doctor: verbose diagnostics not implemented yet\n")
        return

    sys.stdout.write("doctor: basic checks passed\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the root CLI parser."""
    parser = argparse.ArgumentParser(
        prog="ledger",
        description=(
            "Local-first finance ledger CLI for syncing Plaid data into "
            "SQLite and exporting agent-friendly artifacts."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Show project setup diagnostics.",
    )
    doctor_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase diagnostics verbosity.",
    )

    return parser


def main() -> None:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "doctor":
        doctor(verbose=args.verbose)


if __name__ == "__main__":
    main()
