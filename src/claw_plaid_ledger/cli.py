"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

import typer

app = typer.Typer(
    help_text=(
        "Local-first finance ledger CLI for syncing Plaid data into SQLite "
        "and exporting agent-friendly artifacts."
    ),
)


@app.command()
def doctor(
    verbose: object = typer.Option(0, "--verbose", "-v", count=True),
) -> None:
    """Show environment and setup diagnostics for this project."""
    if not isinstance(verbose, int):
        msg = "verbose must be an integer count"
        raise TypeError(msg)

    verbosity = verbose
    if verbosity > 0:
        typer.echo("doctor: verbose diagnostics not implemented yet")
        return

    typer.echo("doctor: basic checks passed")


def main() -> None:
    """Run the CLI."""
    app()


if __name__ == "__main__":
    main()
