"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

import os
import sqlite3
from typing import Annotated

import typer
import uvicorn

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

_EXPECTED_TABLES = {"accounts", "transactions", "sync_state"}
_REDACT_KEEP_CHARS = 4


def _redact(value: str | None) -> str:
    """Redact a secret value, showing only the last 4 characters."""
    if value is None:
        return "(not set)"
    if len(value) <= _REDACT_KEEP_CHARS:
        return "****"
    return f"****{value[-_REDACT_KEEP_CHARS:]}"


@app.command()
def doctor(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True)] = 0,
) -> None:
    """Show environment and setup diagnostics for this project."""
    # Validate required env vars
    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(f"doctor: env [FAIL] {error}")
        raise SystemExit(1) from error

    typer.echo("doctor: env [OK]")

    # Confirm DB file exists
    if not config.db_path.exists():
        typer.echo(f"doctor: db [FAIL] file not found: {config.db_path}")
        raise SystemExit(1)

    typer.echo(f"doctor: db [OK] {config.db_path}")

    # Verify schema and collect stats; raise outside try to satisfy TRY301
    schema_error: str | None = None
    sync_count = 0
    last_synced: str = "never"
    account_count = 0
    tx_count = 0

    try:
        with sqlite3.connect(config.db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            existing_tables = {row[0] for row in rows}
            missing_tables = _EXPECTED_TABLES - existing_tables
            if missing_tables:
                schema_error = ", ".join(sorted(missing_tables))
            else:
                sync_row = conn.execute(
                    "SELECT COUNT(*), MAX(last_synced_at) FROM sync_state"
                ).fetchone()
                account_count = conn.execute(
                    "SELECT COUNT(*) FROM accounts"
                ).fetchone()[0]
                tx_count = conn.execute(
                    "SELECT COUNT(*) FROM transactions"
                ).fetchone()[0]
                sync_count = sync_row[0]
                last_synced = sync_row[1] or "never"
    except Exception as exc:
        typer.echo(f"doctor: db [FAIL] {exc}")
        raise SystemExit(1) from exc

    if schema_error is not None:
        typer.echo(f"doctor: schema [FAIL] missing tables: {schema_error}")
        raise SystemExit(1)

    typer.echo("doctor: schema [OK]")
    typer.echo(
        f"doctor: sync_state rows={sync_count} last_synced_at={last_synced}"
    )
    typer.echo(f"doctor: accounts rows={account_count}")
    typer.echo(f"doctor: transactions rows={tx_count}")

    if verbose > 0:
        typer.echo(
            f"doctor: config CLAW_PLAID_LEDGER_DB_PATH={config.db_path}"
        )
        typer.echo(
            f"doctor: config PLAID_CLIENT_ID="
            f"{config.plaid_client_id or '(not set)'}"
        )
        typer.echo(
            f"doctor: config PLAID_ENV={config.plaid_env or '(not set)'}"
        )
        typer.echo(
            f"doctor: config PLAID_SECRET={_redact(config.plaid_secret)}"
        )
        typer.echo(
            f"doctor: config PLAID_ACCESS_TOKEN="
            f"{_redact(config.plaid_access_token)}"
        )

    if config.api_secret:
        typer.echo("doctor: CLAW_API_SECRET [OK]")
    else:
        typer.echo("doctor: CLAW_API_SECRET [FAIL] not set")

    typer.echo("doctor: all checks passed")


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
        item_id=config.item_id,
    )
    typer.echo(
        "sync: "
        f"accounts={summary.accounts} "
        f"added={summary.added} "
        f"modified={summary.modified} "
        f"removed={summary.removed}"
    )


@app.command()
def serve() -> None:
    """Start the HTTP server on CLAW_SERVER_HOST:CLAW_SERVER_PORT."""
    if not os.environ.get("CLAW_API_SECRET"):
        typer.echo(
            "serve: CLAW_API_SECRET is not set; refusing to start. "
            "Set CLAW_API_SECRET to a strong random secret before "
            "running the server."
        )
        raise SystemExit(1)

    host = os.environ.get("CLAW_SERVER_HOST", "127.0.0.1")
    port_str = os.environ.get("CLAW_SERVER_PORT", "8000")
    try:
        port = int(port_str)
    except ValueError as exc:
        typer.echo(f"serve: invalid CLAW_SERVER_PORT value: {port_str!r}")
        raise SystemExit(1) from exc
    uvicorn.run("claw_plaid_ledger.server:app", host=host, port=port)


def main() -> None:
    """Run the CLI."""
    app()


if __name__ == "__main__":
    main()
