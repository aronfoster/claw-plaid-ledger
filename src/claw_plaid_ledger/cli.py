"""CLI entrypoints for claw-plaid-ledger."""

from typing import Annotated

import typer

app = typer.Typer(
    name="ledger",
    help=(
        "Local-first finance ledger CLI for syncing Plaid data into "
        "SQLite and exporting agent-friendly artifacts."
    ),
    no_args_is_help=True,
)


@app.command()
def doctor(
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase diagnostics verbosity.",
        ),
    ] = 0,
) -> None:
    """Show environment and setup diagnostics for this project."""
    if verbose > 0:
        typer.echo("doctor: verbose diagnostics not implemented yet")
        return

    typer.echo("doctor: basic checks passed")


if __name__ == "__main__":
    app()
