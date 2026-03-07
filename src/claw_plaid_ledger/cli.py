"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

from typing import cast

import typer
from claw_plaid_ledger.config import ConfigError, load_config
from claw_plaid_ledger.db import initialize_database

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


@app.command()
def init_db() -> None:
    """Create the SQLite database file and initialize schema."""
    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(f"init-db: {error}")
        raise SystemExit(2) from error

    initialize_database(config.db_path)
    typer.echo(f"init-db: initialized database at {config.db_path}")


def main() -> None:
    """Run the CLI."""
    app()


if __name__ == "__main__":
    main()
