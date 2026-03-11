"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

import logging
import os
import sqlite3
import webbrowser
from typing import Annotated, cast

import typer
import uvicorn

from claw_plaid_ledger.config import (
    _VALID_LOG_LEVELS,
    Config,
    ConfigError,
    load_config,
)
from claw_plaid_ledger.db import get_all_sync_state, initialize_database
from claw_plaid_ledger.items_config import (
    ItemConfig,
    ItemsConfigError,
    load_items_config,
)
from claw_plaid_ledger.link_server import (
    LINK_SERVER_HOST,
    LINK_SERVER_PORT,
    start_link_server,
)
from claw_plaid_ledger.plaid_adapter import PlaidClientAdapter
from claw_plaid_ledger.preflight import (
    CheckSeverity,
    CheckStatus,
    run_production_preflight,
)
from claw_plaid_ledger.sync_engine import SyncSummary, run_sync

app = typer.Typer(
    help=(
        "Local-first finance ledger CLI for syncing Plaid data into "
        "SQLite and exporting agent-friendly artifacts."
    ),
)


def _doctor_verbose_config(config: Config) -> None:
    """Print verbose config values for the doctor command."""
    typer.echo(f"doctor: config CLAW_PLAID_LEDGER_DB_PATH={config.db_path}")
    typer.echo(
        f"doctor: config PLAID_CLIENT_ID="
        f"{config.plaid_client_id or '(not set)'}"
    )
    typer.echo(f"doctor: config PLAID_ENV={config.plaid_env or '(not set)'}")
    typer.echo(f"doctor: config PLAID_SECRET={_redact(config.plaid_secret)}")
    typer.echo(
        f"doctor: config PLAID_ACCESS_TOKEN="
        f"{_redact(config.plaid_access_token)}"
    )


def _doctor_openclaw_check(config: Config) -> None:
    """Report OpenClaw notification configuration status."""
    if config.openclaw_hooks_token is not None:
        url = config.openclaw_hooks_url
        agent = config.openclaw_hooks_agent
        typer.echo(
            f"doctor: openclaw notification [OK] url={url} agent={agent}"
        )
    else:
        typer.echo(
            "doctor: openclaw notification [WARN] "
            "OPENCLAW_HOOKS_TOKEN not set \u2014 notifications disabled"
        )


def _load_doctor_items_config() -> tuple[
    list[ItemConfig] | None, ItemsConfigError | None
]:
    """Load items.toml for doctor, returning either items or a parse error."""
    try:
        items_config = load_items_config()
    except ItemsConfigError as error:
        return None, error
    return items_config, None


def _doctor_per_item_sync_state(
    *,
    db_path: os.PathLike[str] | str,
    items_config: list[ItemConfig],
) -> None:
    """Print per-item sync state by joining items.toml with sync_state."""
    with sqlite3.connect(db_path) as conn:
        sync_state_rows = get_all_sync_state(conn)

    sync_state_by_item_id = {row.item_id: row for row in sync_state_rows}
    configured_item_ids = {item_cfg.id for item_cfg in items_config}

    for item_cfg in items_config:
        sync_state_row = sync_state_by_item_id.get(item_cfg.id)
        if sync_state_row is None:
            last_synced_at = "never"
        else:
            last_synced_at = sync_state_row.last_synced_at or "never"
        typer.echo(
            f"doctor: item {item_cfg.id} "
            f"owner={item_cfg.owner} "
            f"last_synced_at={last_synced_at}"
        )

    for sync_state_row in sync_state_rows:
        if sync_state_row.item_id in configured_item_ids:
            continue
        last_synced_at = sync_state_row.last_synced_at or "never"
        typer.echo(
            f"doctor: item {sync_state_row.item_id} "
            f"owner={sync_state_row.owner} "
            f"last_synced_at={last_synced_at} [not in items.toml]"
        )


_EXPECTED_TABLES = {"accounts", "transactions", "sync_state"}
_REDACT_KEEP_CHARS = 4


def _doctor_db_stats(
    db_path: os.PathLike[str] | str,
) -> tuple[str | None, int, str, int, int]:
    """Collect schema status and row counts for doctor output."""
    schema_error: str | None = None
    sync_count = 0
    last_synced = "never"
    account_count = 0
    tx_count = 0

    with sqlite3.connect(db_path) as conn:
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

    return schema_error, sync_count, last_synced, account_count, tx_count


def _redact(value: str | None) -> str:
    """Redact a secret value, showing only the last 4 characters."""
    if value is None:
        return "(not set)"
    if len(value) <= _REDACT_KEEP_CHARS:
        return "****"
    return f"****{value[-_REDACT_KEEP_CHARS:]}"


def _doctor_run_preflight() -> None:
    """Run production preflight checks and print results."""
    results = run_production_preflight()
    failure_count = 0
    for result in results:
        typer.echo(
            f"preflight: {result.name} "
            f"[{result.status.value}] {result.message}"
        )
        if (
            result.status is CheckStatus.FAIL
            and result.severity is CheckSeverity.REQUIRED
        ):
            failure_count += 1
    if failure_count > 0:
        typer.echo(
            f"preflight: {failure_count} check(s) failed"
            " \u2014 not production-ready"
        )
        raise SystemExit(1)
    typer.echo("preflight: all required checks passed")


@app.command()
def doctor(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True)] = 0,
    production_preflight: Annotated[
        int, typer.Option("--production-preflight", count=True)
    ] = 0,
) -> None:
    """Show environment and setup diagnostics for this project."""
    if production_preflight > 0:
        _doctor_run_preflight()
        return

    # Validate required env vars
    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(f"doctor: env [FAIL] {error}")
        raise SystemExit(1) from error

    typer.echo("doctor: env [OK]")

    items_config, items_config_error = _load_doctor_items_config()

    # Confirm DB file exists
    if not config.db_path.exists():
        typer.echo(f"doctor: db [FAIL] file not found: {config.db_path}")
        raise SystemExit(1)

    typer.echo(f"doctor: db [OK] {config.db_path}")

    # Verify schema and collect stats; raise outside try to satisfy TRY301
    try:
        (
            schema_error,
            sync_count,
            last_synced,
            account_count,
            tx_count,
        ) = _doctor_db_stats(config.db_path)
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

    if items_config_error is not None:
        typer.echo(
            f"doctor: items.toml [WARN] parse error: {items_config_error}"
        )
    elif items_config == []:
        typer.echo("doctor: items.toml not found — single-item mode")
    else:
        _doctor_per_item_sync_state(
            db_path=config.db_path,
            items_config=cast("list[ItemConfig]", items_config),
        )

    if verbose > 0:
        _doctor_verbose_config(config)

    if config.api_secret:
        typer.echo("doctor: CLAW_API_SECRET [OK]")
    else:
        typer.echo("doctor: CLAW_API_SECRET [FAIL] not set")

    _doctor_openclaw_check(config)

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


def _sync_summary(prefix: str, summary: SyncSummary) -> None:
    """Print a standard sync summary line."""
    typer.echo(
        f"{prefix}: "
        f"accounts={summary.accounts} "
        f"added={summary.added} "
        f"modified={summary.modified} "
        f"removed={summary.removed}"
    )


def _sync_default_mode() -> None:
    """Run legacy single-item sync using PLAID_ACCESS_TOKEN."""
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
    _sync_summary("sync", summary)


def _load_client_config_for_sync() -> Config:
    """Load config for sync paths that only need shared Plaid client vars."""
    try:
        return load_config(require_plaid_client=True)
    except ConfigError as error:
        typer.echo(f"sync: {error}")
        raise SystemExit(2) from error


def _sync_named_item(item_id: str) -> None:
    """Run sync for exactly one item from items.toml."""
    items_config = load_items_config()
    item_cfg = next((cfg for cfg in items_config if cfg.id == item_id), None)
    if item_cfg is None:
        typer.echo(f"sync: item '{item_id}' not found in items.toml")
        raise SystemExit(2)

    token = os.environ.get(item_cfg.access_token_env)
    if token is None:
        typer.echo(f"sync: {item_cfg.access_token_env} is not set")
        raise SystemExit(2)

    config = _load_client_config_for_sync()
    adapter = PlaidClientAdapter.from_config(config)
    summary = run_sync(
        db_path=config.db_path,
        adapter=adapter,
        access_token=token,
        item_id=item_cfg.id,
        owner=item_cfg.owner,
    )
    _sync_summary(f"sync[{item_cfg.id}]", summary)


def _sync_all_items() -> None:
    """Run sync sequentially for all items in items.toml."""
    items_config = load_items_config()
    if len(items_config) == 0:
        typer.echo("sync --all: no items found in items.toml")
        raise SystemExit(2)

    config = _load_client_config_for_sync()
    adapter = PlaidClientAdapter.from_config(config)
    success_count = 0
    failure_count = 0

    for item_cfg in items_config:
        token = os.environ.get(item_cfg.access_token_env)
        if token is None:
            typer.echo(
                "sync["
                f"{item_cfg.id}"
                "]: ERROR "
                f"{item_cfg.access_token_env} is not set"
            )
            failure_count += 1
            continue

        try:
            summary = run_sync(
                db_path=config.db_path,
                adapter=adapter,
                access_token=token,
                item_id=item_cfg.id,
                owner=item_cfg.owner,
            )
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
            typer.echo(f"sync[{item_cfg.id}]: ERROR {exc}")
            failure_count += 1
            continue

        _sync_summary(f"sync[{item_cfg.id}]", summary)
        success_count += 1

    typer.echo(
        f"sync --all: {success_count} items synced, {failure_count} failed"
    )
    if failure_count > 0:
        raise SystemExit(1)


@app.command()
def sync(
    item: Annotated[
        str | None,
        typer.Option(
            "--item", help="Sync a single item from items.toml by ID."
        ),
    ] = None,
    all_items: Annotated[
        int,
        typer.Option(
            "--all", count=True, help="Sync all items listed in items.toml."
        ),
    ] = 0,
) -> None:
    """Sync transactions from Plaid into the local SQLite ledger."""
    if item is not None and all_items > 0:
        typer.echo("sync: --item and --all are mutually exclusive")
        raise SystemExit(2)

    if item is None and all_items == 0:
        _sync_default_mode()
        return

    if item is not None:
        _sync_named_item(item)
        return

    _sync_all_items()


def _print_link_result(access_token: str, item_id: str) -> None:
    """Print the exchange result and a sample items.toml snippet."""
    typer.echo("\nLink complete. Exchanging token...\n")
    typer.echo(f"  access_token : {access_token}")
    typer.echo(f"  item_id      : {item_id}")
    typer.echo("\nAdd to items.toml and set the matching env var:\n")
    typer.echo("  [[items]]")
    typer.echo('  id                = "bank-alice"')
    typer.echo('  access_token_env  = "PLAID_ACCESS_TOKEN_BANK_ALICE"')
    typer.echo('  owner             = "alice"')
    typer.echo("")
    typer.echo(f'  export PLAID_ACCESS_TOKEN_BANK_ALICE="{access_token}"')


@app.command()
def link(
    products: Annotated[
        list[str] | None,
        typer.Option(
            "--products",
            help=(
                "Plaid product to request (e.g. transactions). "
                "May be repeated."
            ),
        ),
    ] = None,
) -> None:
    """Connect a Plaid institution via browser and print the access token."""
    requested_products = products if products is not None else ["transactions"]

    try:
        config = load_config(require_plaid_client=True)
    except ConfigError as error:
        typer.echo(f"link: {error}")
        raise SystemExit(2) from error

    adapter = PlaidClientAdapter.from_config(config)

    typer.echo("Creating Plaid link token...")
    try:
        link_token = adapter.create_link_token(
            "operator",
            requested_products,
            ["US"],
        )
    except (RuntimeError, OSError) as error:
        typer.echo(f"link: failed to create link token: {error}")
        raise SystemExit(1) from error

    typer.echo(
        f"Starting local Link server at "
        f"http://{LINK_SERVER_HOST}:{LINK_SERVER_PORT}"
    )
    server, done_event, result_container = start_link_server(link_token)

    typer.echo(
        "Opening browser \u2014 complete the Plaid Link flow to "
        "connect your institution."
    )
    webbrowser.open(f"http://{LINK_SERVER_HOST}:{LINK_SERVER_PORT}")

    try:
        done_event.wait()
    except KeyboardInterrupt:
        typer.echo("\nlink: interrupted by user")
        server.shutdown()
        raise SystemExit(1) from None

    server.shutdown()

    if not result_container:
        typer.echo("link: no token received from browser")
        raise SystemExit(1)

    public_token = result_container[0]

    try:
        access_token, item_id = adapter.exchange_public_token(public_token)
    except (RuntimeError, OSError) as error:
        typer.echo(f"link: failed to exchange token: {error}")
        raise SystemExit(1) from error

    _print_link_result(access_token, item_id)


def _items_query_db(
    db_path: os.PathLike[str] | str,
    item_id: str,
) -> tuple[int, str]:
    """Return (account_count, last_synced) for one item from the DB."""
    try:
        with sqlite3.connect(db_path) as conn:
            acct_row = conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            sync_row = conn.execute(
                "SELECT last_synced_at FROM sync_state WHERE item_id = ?",
                (item_id,),
            ).fetchone()
    except sqlite3.Error:
        return 0, "never"
    account_count = int(acct_row[0]) if acct_row else 0
    last_synced = str(sync_row[0]) if sync_row and sync_row[0] else "never"
    return account_count, last_synced


@app.command()
def items() -> None:
    """Show health status for all configured Plaid items."""
    try:
        items_config = load_items_config()
    except ItemsConfigError as error:
        typer.echo(f"items: parse error: {error}")
        raise SystemExit(1) from error

    if not items_config:
        typer.echo("items: no items configured \u2014 create items.toml")
        return

    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(f"items: {error}")
        raise SystemExit(1) from error

    healthy = 0
    total = len(items_config)

    for item_cfg in items_config:
        token_val = os.environ.get(item_cfg.access_token_env)
        token_status = "SET" if token_val else "MISSING"
        if token_val:
            healthy += 1

        account_count, last_synced = _items_query_db(
            config.db_path, item_cfg.id
        )

        owner_str = item_cfg.owner if item_cfg.owner is not None else "(none)"
        typer.echo(
            f"items: {item_cfg.id} "
            f"owner={owner_str} "
            f"token={token_status} "
            f"accounts={account_count} "
            f"last_synced={last_synced}"
        )

    need_attention = total - healthy
    typer.echo(
        f"items: {healthy}/{total} items healthy, "
        f"{need_attention} need attention"
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

    log_level_raw = (os.environ.get("CLAW_LOG_LEVEL") or "INFO").upper()
    if log_level_raw not in _VALID_LOG_LEVELS:
        typer.echo(
            f"serve: invalid CLAW_LOG_LEVEL={log_level_raw!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}"
        )
        raise SystemExit(1)

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=getattr(logging, log_level_raw),
    )

    host = os.environ.get("CLAW_SERVER_HOST", "127.0.0.1")
    port_str = os.environ.get("CLAW_SERVER_PORT", "8000")
    try:
        port = int(port_str)
    except ValueError as exc:
        typer.echo(f"serve: invalid CLAW_SERVER_PORT value: {port_str!r}")
        raise SystemExit(1) from exc

    _serve_logger = logging.getLogger(__name__)
    _serve_logger.info(
        "server starting host=%s port=%d log_level=%s",
        host,
        port,
        log_level_raw,
    )

    uvicorn.run("claw_plaid_ledger.server:app", host=host, port=port)


def main() -> None:
    """Run the CLI."""
    app()


if __name__ == "__main__":
    main()
