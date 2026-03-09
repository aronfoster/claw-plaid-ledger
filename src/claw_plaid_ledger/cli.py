"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

from typing import Annotated

import typer

from claw_plaid_ledger.config import ConfigError, load_config
from claw_plaid_ledger.db import initialize_database
from claw_plaid_ledger.plaid_adapter import PlaidClientAdapter
from claw_plaid_ledger.sync_engine import run_sync

app = typer.Typer(
    help=(
        "Local-first finance ledger CLI for syncing Plaid data into "
        "SQLite and exporting agent-friendly artifacts."
    ),
)


@app.command()
def doctor(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True)] = 0,
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


@app.command()
def sync() -> None:
    """Sync transactions from Plaid into the local SQLite ledger."""
    try:
        config = load_config(require_plaid=True)
    except ConfigError as error:
        typer.echo(f"sync: {error}")
        raise SystemExit(2) from error

    if config.plaid_access_token is None:
        message = (
            "Missing required environment variable(s): PLAID_ACCESS_TOKEN"
        )
        typer.echo(f"sync: {message}")
        raise SystemExit(2)

    adapter = PlaidClientAdapter.from_config(config)
    summary = run_sync(
        db_path=config.db_path,
        adapter=adapter,
        access_token=config.plaid_access_token,
    )
    typer.echo(
        "sync: "
        f"accounts={summary.accounts} "
        f"added={summary.added} "
        f"modified={summary.modified} "
        f"removed={summary.removed}"
    )


def main() -> None:
    """Run the CLI."""
    app()


if __name__ == "__main__":
    main()
