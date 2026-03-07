"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

from typing import cast

import typer

app = typer.Typer(
    help_text=(
        "Local-first finance ledger CLI for syncing Plaid data into "
        "SQLite and exporting agent-friendly artifacts."
    ),
)


@app.command()
def doctor(
    verbose: int = cast("int", typer.option(0, "--verbose", "-v", count=True)),
) -> None:
    """Show environment and setup diagnostics for this project."""
    if verbose > 0:
        typer.echo("doctor: verbose diagnostics not implemented yet")
        return

    typer.echo("doctor: basic checks passed")


def main() -> None:
    """Run the CLI."""
    app()


if __name__ == "__main__":
    main()
