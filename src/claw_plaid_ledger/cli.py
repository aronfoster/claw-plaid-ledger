"""CLI entrypoints for claw-plaid-ledger."""

from __future__ import annotations

import http
import json
import logging
import sqlite3
import sys
import uuid
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

if TYPE_CHECKING:
    import os

import httpx
import typer
import uvicorn

from claw_plaid_ledger.config import (
    _VALID_LOG_LEVELS,
    Config,
    ConfigError,
    load_api_secret,
    load_config,
    load_merged_env,
)
from claw_plaid_ledger.db import (
    apply_account_precedence,
    get_all_sync_state,
    initialize_database,
)
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
from claw_plaid_ledger.logging_utils import (
    CorrelationIdFilter,
    set_correlation_id,
)
from claw_plaid_ledger.plaid_adapter import PlaidClientAdapter
from claw_plaid_ledger.preflight import (
    CheckSeverity,
    CheckStatus,
    run_production_preflight,
)
from claw_plaid_ledger.sync_engine import (
    PlaidPermanentError,
    PlaidTransientError,
    SyncSummary,
    run_sync,
)

app = typer.Typer(
    help=(
        "Local-first finance ledger CLI for syncing Plaid data into "
        "SQLite and exporting agent-friendly artifacts."
    ),
)

_sync_logger = logging.getLogger(__name__)


_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s [%(correlation_id)s]: %(message)s"
)


def _setup_sync_logging() -> None:
    """
    Configure basic logging for CLI sync commands if not already set up.

    This ensures sync_run_id appears in log output for manual syncs.
    Calling basicConfig when handlers are already configured is a no-op,
    so this is safe to call unconditionally.
    """
    logging.basicConfig(format=_LOG_FORMAT, level=logging.INFO)
    for handler in logging.root.handlers:
        if not any(
            isinstance(f, CorrelationIdFilter) for f in handler.filters
        ):
            handler.addFilter(CorrelationIdFilter())


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


def _doctor_webhook_check(config: Config) -> None:
    """Report webhook and dual-enablement status (informational only)."""
    if config.webhook_enabled:
        typer.echo("doctor: webhooks: ENABLED (CLAW_WEBHOOK_ENABLED=true)")
    else:
        typer.echo("doctor: webhooks: DISABLED (default)")
    if config.webhook_enabled and config.scheduled_sync_enabled:
        typer.echo(
            "doctor: [WARN] both webhooks and scheduled-sync are enabled"
            " — this is unusual; see RUNBOOK.md"
        )


def _doctor_scheduled_sync_check(config: Config) -> None:
    """Report scheduled sync configuration status (informational only)."""
    if config.scheduled_sync_enabled:
        typer.echo(
            f"doctor: scheduled-sync: ENABLED \u2014 fallback window"
            f" {config.scheduled_sync_fallback_hours}h, check interval 60min"
        )
    else:
        typer.echo(
            "doctor: scheduled-sync: DISABLED"
            " (set CLAW_SCHEDULED_SYNC_ENABLED=true to enable)"
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


_EXPECTED_TABLES = {"accounts", "transactions", "sync_state", "ledger_errors"}
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


def _doctor_error_log_stats(db_path: Path) -> tuple[int, int]:
    """Return (warn_count, error_count) for the last 24 hours."""
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    with sqlite3.connect(db_path) as conn:
        warn = conn.execute(
            "SELECT COUNT(*) FROM ledger_errors "
            "WHERE severity = 'WARNING' AND created_at >= ?",
            (cutoff,),
        ).fetchone()[0]
        error = conn.execute(
            "SELECT COUNT(*) FROM ledger_errors "
            "WHERE severity IN ('ERROR', 'CRITICAL') AND created_at >= ?",
            (cutoff,),
        ).fetchone()[0]
    return int(warn), int(error)


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
    warn_count, error_count = _doctor_error_log_stats(config.db_path)
    typer.echo(
        f"doctor: error-log warn={warn_count} error={error_count} (last 24h)"
    )

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
    _doctor_webhook_check(config)
    _doctor_scheduled_sync_check(config)

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
    _setup_sync_logging()
    sync_run_id = "sync-" + uuid.uuid4().hex[:8]
    set_correlation_id(sync_run_id)
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

    _sync_logger.info("sync starting sync_run_id=%s", sync_run_id)
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
    _setup_sync_logging()
    sync_run_id = "sync-" + uuid.uuid4().hex[:8]
    set_correlation_id(sync_run_id)
    items_config = load_items_config()
    item_cfg = next((cfg for cfg in items_config if cfg.id == item_id), None)
    if item_cfg is None:
        typer.echo(f"sync: item '{item_id}' not found in items.toml")
        raise SystemExit(2)

    merged_env = load_merged_env()
    token = merged_env.get(item_cfg.access_token_env)
    if token is None:
        typer.echo(f"sync: {item_cfg.access_token_env} is not set")
        raise SystemExit(2)

    _sync_logger.info(
        "sync starting item_id=%s sync_run_id=%s", item_id, sync_run_id
    )
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
    _setup_sync_logging()
    items_config = load_items_config()
    if len(items_config) == 0:
        typer.echo("sync --all: no items found in items.toml")
        raise SystemExit(2)

    config = _load_client_config_for_sync()
    adapter = PlaidClientAdapter.from_config(config)
    success_count = 0
    failure_count = 0
    merged_env = load_merged_env()

    for item_cfg in items_config:
        token = merged_env.get(item_cfg.access_token_env)
        if token is None:
            typer.echo(
                "sync["
                f"{item_cfg.id}"
                "]: ERROR "
                f"{item_cfg.access_token_env} is not set"
            )
            failure_count += 1
            continue

        sync_run_id = "sync-" + uuid.uuid4().hex[:8]
        set_correlation_id(sync_run_id)
        _sync_logger.info(
            "sync starting item_id=%s sync_run_id=%s",
            item_cfg.id,
            sync_run_id,
        )
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


def _refresh_default_mode() -> None:
    """Run refresh for the singleton PLAID_ACCESS_TOKEN item."""
    try:
        config = load_config(require_plaid=True)
    except ConfigError as error:
        typer.echo(f"refresh: {error}")
        raise SystemExit(2) from error

    if config.plaid_access_token is None:
        message = (
            "Missing required environment variable(s): PLAID_ACCESS_TOKEN"
        )
        typer.echo(f"refresh: {message}")
        raise SystemExit(2)

    adapter = PlaidClientAdapter.from_config(config)
    try:
        adapter.refresh_transactions(config.plaid_access_token)
    except (PlaidPermanentError, PlaidTransientError) as exc:
        typer.echo(f"refresh: ERROR {exc}")
        raise SystemExit(1) from exc
    typer.echo("refresh: OK")


def _refresh_named_item(item_id: str) -> None:
    """Run refresh for exactly one item from items.toml."""
    items_config = load_items_config()
    item_cfg = next((cfg for cfg in items_config if cfg.id == item_id), None)
    if item_cfg is None:
        typer.echo(f"refresh: item '{item_id}' not found in items.toml")
        raise SystemExit(2)

    merged_env = load_merged_env()
    token = merged_env.get(item_cfg.access_token_env)
    if token is None:
        typer.echo(f"refresh: {item_cfg.access_token_env} is not set")
        raise SystemExit(2)

    config = _load_client_config_for_sync()
    adapter = PlaidClientAdapter.from_config(config)
    try:
        adapter.refresh_transactions(token)
    except (PlaidPermanentError, PlaidTransientError) as exc:
        typer.echo(f"refresh[{item_cfg.id}]: ERROR {exc}")
        raise SystemExit(1) from exc
    typer.echo(f"refresh[{item_cfg.id}]: OK")


def _refresh_all_items() -> None:
    """Run refresh sequentially for all items in items.toml."""
    items_config = load_items_config()
    if len(items_config) == 0:
        typer.echo("refresh --all: no items found in items.toml")
        raise SystemExit(2)

    config = _load_client_config_for_sync()
    adapter = PlaidClientAdapter.from_config(config)
    success_count = 0
    failure_count = 0
    merged_env = load_merged_env()

    for item_cfg in items_config:
        token = merged_env.get(item_cfg.access_token_env)
        if token is None:
            typer.echo(
                f"refresh[{item_cfg.id}]: ERROR "
                f"{item_cfg.access_token_env} is not set"
            )
            failure_count += 1
            continue

        try:
            adapter.refresh_transactions(token)
        except (RuntimeError, OSError) as exc:
            typer.echo(f"refresh[{item_cfg.id}]: ERROR {exc}")
            failure_count += 1
            continue

        typer.echo(f"refresh[{item_cfg.id}]: OK")
        success_count += 1

    typer.echo(
        f"refresh --all: {success_count} items refreshed,"
        f" {failure_count} failed"
    )
    if failure_count > 0:
        raise SystemExit(1)


@app.command()
def refresh(
    item: Annotated[
        str | None,
        typer.Option(
            "--item", help="Refresh a single item from items.toml by ID."
        ),
    ] = None,
    all_items: Annotated[
        int,
        typer.Option(
            "--all", count=True, help="Refresh all items listed in items.toml."
        ),
    ] = 0,
) -> None:
    """Ask Plaid to re-check institutions and fire SYNC_UPDATES_AVAILABLE."""
    if item is not None and all_items > 0:
        typer.echo("refresh: --item and --all are mutually exclusive")
        raise SystemExit(2)

    if item is None and all_items == 0:
        _refresh_default_mode()
        return

    if item is not None:
        _refresh_named_item(item)
        return

    _refresh_all_items()


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
    webhook: Annotated[
        str | None,
        typer.Option(
            "--webhook",
            help=(
                "Webhook URL to register on the new item "
                "(e.g. https://example.com/webhooks/plaid). "
                "Plaid will send transaction events here without needing "
                "a separate 'ledger webhook-set' call."
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
        extra = {"webhook": webhook} if webhook is not None else {}
        link_token = adapter.create_link_token(
            "operator",
            requested_products,
            ["US"],
            **extra,
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


def _webhook_set_all(adapter: PlaidClientAdapter, url: str) -> None:
    """Update the webhook URL for every item in items.toml."""
    try:
        items_cfg = load_items_config()
    except ItemsConfigError as error:
        typer.echo(f"webhook-set: items.toml error: {error}")
        raise SystemExit(1) from error

    if not items_cfg:
        typer.echo("webhook-set: no items found in items.toml")
        raise SystemExit(1)

    merged_env = load_merged_env()
    ok = 0
    for item_cfg in items_cfg:
        token = merged_env.get(item_cfg.access_token_env)
        if not token:
            typer.echo(
                f"webhook-set: {item_cfg.id}: "
                f"env var {item_cfg.access_token_env} not set — skipped"
            )
            continue
        try:
            adapter.update_item_webhook(token, url)
            typer.echo(f"webhook-set: {item_cfg.id}: updated to {url}")
            ok += 1
        except (RuntimeError, OSError) as error:
            typer.echo(f"webhook-set: {item_cfg.id}: failed: {error}")

    typer.echo(f"webhook-set: {ok}/{len(items_cfg)} items updated")
    if ok < len(items_cfg):
        raise SystemExit(1)


def _webhook_set_single(adapter: PlaidClientAdapter, url: str) -> None:
    """Update the webhook URL for the single item in PLAID_ACCESS_TOKEN."""
    try:
        config_full = load_config(require_plaid=True)
    except ConfigError as error:
        typer.echo(f"webhook-set: {error}")
        raise SystemExit(2) from error

    token = config_full.plaid_access_token
    if not token:
        typer.echo("webhook-set: PLAID_ACCESS_TOKEN not set")
        raise SystemExit(2)

    try:
        adapter.update_item_webhook(token, url)
    except (RuntimeError, OSError) as error:
        typer.echo(f"webhook-set: failed: {error}")
        raise SystemExit(1) from error

    typer.echo(f"webhook-set: updated to {url}")


@app.command(name="webhook-set")
def webhook_set(
    url: Annotated[
        str,
        typer.Option("--url", help="Webhook URL to register with Plaid."),
    ],
    all_items: Annotated[
        int,
        typer.Option(
            "--all",
            count=True,
            help="Update all items listed in items.toml.",
        ),
    ] = 0,
) -> None:
    """
    Register or update the webhook URL on existing Plaid items.

    Use this after setting up a public webhook URL to backfill items that were
    linked before the webhook was configured.  Without this, Plaid will not
    send transaction events to your server for those items.

    Single-item mode (default): reads PLAID_ACCESS_TOKEN from config.
    Multi-item mode (--all): reads every item from items.toml.
    """
    try:
        config = load_config(require_plaid_client=True)
    except ConfigError as error:
        typer.echo(f"webhook-set: {error}")
        raise SystemExit(2) from error

    adapter = PlaidClientAdapter.from_config(config)

    if all_items > 0:
        _webhook_set_all(adapter, url)
    else:
        _webhook_set_single(adapter, url)


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
    merged_env = load_merged_env()

    for item_cfg in items_config:
        token_val = merged_env.get(item_cfg.access_token_env)
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


@app.command(name="apply-precedence")
def apply_precedence() -> None:
    """Write suppression decisions from items.toml to the DB."""
    try:
        items_config = load_items_config()
    except ItemsConfigError as error:
        typer.echo(f"apply-precedence: config error: {error}")
        raise SystemExit(1) from error

    alias_count = sum(len(item.suppressed_accounts) for item in items_config)

    if not items_config or alias_count == 0:
        typer.echo(
            "apply-precedence: no suppressions configured in items.toml"
        )
        raise SystemExit(0)

    typer.echo(
        f"apply-precedence: loaded {alias_count} alias(es) from items.toml"
    )

    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(f"apply-precedence: {error}")
        raise SystemExit(1) from error

    try:
        with sqlite3.connect(config.db_path) as connection:
            updated = apply_account_precedence(connection, items_config)
    except sqlite3.Error as error:
        typer.echo(f"apply-precedence: DB error: {error}")
        raise SystemExit(1) from error

    skipped = alias_count - updated
    typer.echo(f"apply-precedence: updated {updated} account(s)")
    if skipped > 0:
        typer.echo(
            f"apply-precedence: {skipped} alias(es) skipped "
            "\u2014 account not yet in DB (sync first)"
        )
    typer.echo("apply-precedence: done")


def _find_potential_overlaps(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str, str]]:
    """Return possible overlap groups keyed by name/mask/type."""
    rows = connection.execute(
        "SELECT name, mask, type, "
        "group_concat(DISTINCT item_id) AS items "
        "FROM accounts "
        "WHERE item_id IS NOT NULL AND mask IS NOT NULL "
        "GROUP BY name, mask, type "
        "HAVING COUNT(DISTINCT item_id) > 1 "
        "ORDER BY name, mask, type"
    ).fetchall()
    return [
        (str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows
    ]


@app.command()
def overlaps() -> None:
    """Show suppression config status and potential account overlaps."""
    try:
        items_config = load_items_config()
    except ItemsConfigError as error:
        typer.echo(f"overlaps: config error: {error}")
        raise SystemExit(1) from error

    configured = [
        (item.id, suppression)
        for item in items_config
        for suppression in item.suppressed_accounts
    ]
    if not configured:
        typer.echo("overlaps: no suppressions configured")
        raise SystemExit(0)

    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(f"overlaps: {error}")
        raise SystemExit(1) from error

    try:
        with sqlite3.connect(config.db_path) as connection:
            account_rows = connection.execute(
                "SELECT plaid_account_id, canonical_account_id FROM accounts"
            ).fetchall()
            overlap_rows = _find_potential_overlaps(connection)
    except sqlite3.Error as error:
        typer.echo(f"overlaps: DB error: {error}")
        raise SystemExit(1) from error

    account_status_by_id = {str(row[0]): row[1] for row in account_rows}
    active_count = 0
    pending_count = 0

    typer.echo("Configured suppressions (from items.toml):")
    for item_id, suppression in configured:
        current_canonical = account_status_by_id.get(
            suppression.plaid_account_id
        )
        if current_canonical is None:
            status = "NOT YET SYNCED — run sync first"
            pending_count += 1
        elif current_canonical == suppression.canonical_account_id:
            status = "IN DB"
            active_count += 1
        else:
            status = "MISMATCH"

        canonical_from = (
            f" ({suppression.canonical_from_item})"
            if suppression.canonical_from_item is not None
            else ""
        )
        typer.echo(
            "  "
            f"{item_id} / {suppression.plaid_account_id}  →  "
            f"suppressed by {suppression.canonical_account_id}"
            f"{canonical_from}"
            f"  [{status}]"
        )

    typer.echo("")
    typer.echo(
        "Potential unconfirmed overlaps "
        "(same name + mask from different items):"
    )
    if not overlap_rows:
        typer.echo("  none detected")
    else:
        for name, mask, account_type, items_csv in overlap_rows:
            items_list = ", ".join(sorted(items_csv.split(",")))
            typer.echo(
                f'  "{name}"  mask={mask}  type={account_type}  '
                f"items: {items_list}  — consider adding "
                "suppressed_accounts config"
            )

    typer.echo("")
    typer.echo(
        "overlaps: "
        f"{active_count} configured suppression active, "
        f"{pending_count} pending sync, "
        f"{len(overlap_rows)} potential overlap flagged."
    )


@app.command()
def serve() -> None:
    """Start the HTTP server on CLAW_SERVER_HOST:CLAW_SERVER_PORT."""
    env = load_merged_env()

    if not env.get("CLAW_API_SECRET"):
        typer.echo(
            "serve: CLAW_API_SECRET is not set; refusing to start. "
            "Set CLAW_API_SECRET to a strong random secret before "
            "running the server."
        )
        raise SystemExit(1)

    log_level_raw = (env.get("CLAW_LOG_LEVEL") or "INFO").upper()
    if log_level_raw not in _VALID_LOG_LEVELS:
        typer.echo(
            f"serve: invalid CLAW_LOG_LEVEL={log_level_raw!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}"
        )
        raise SystemExit(1)

    logging.basicConfig(
        format=_LOG_FORMAT,
        level=getattr(logging, log_level_raw),
    )
    for handler in logging.root.handlers:
        handler.addFilter(CorrelationIdFilter())

    host = env.get("CLAW_SERVER_HOST") or "127.0.0.1"
    port_str = env.get("CLAW_SERVER_PORT") or "8000"
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


allocations_app = typer.Typer(help="Manage transaction allocations.")
app.add_typer(allocations_app, name="allocations")

_ALLOC_SEPARATOR = "\u2500" * 46


def _allocations_api_config() -> tuple[str, str]:
    """Return (base_url, api_secret) for the allocations HTTP client."""
    env = load_merged_env()
    base_url = (
        env.get("CLAW_API_BASE_URL") or "http://127.0.0.1:8000"
    ).rstrip("/")
    secret = load_api_secret() or ""
    return base_url, secret


def _alloc_amount(alloc: dict[str, object]) -> float:
    """Safely extract the float amount from an allocation dict."""
    raw = alloc.get("amount")
    return float(raw) if isinstance(raw, (int, float)) else 0.0


def _format_alloc_row(index: int, alloc: dict[str, object]) -> str:
    """Format one allocation row for display."""
    amount = _alloc_amount(alloc)
    cat_raw = alloc.get("category")
    category = str(cat_raw) if cat_raw else "(uncategorised)"
    raw_tags = alloc.get("tags")
    tags_str = (
        "[" + ", ".join(str(t) for t in raw_tags) + "]"
        if isinstance(raw_tags, list)
        else "(no tags)"
    )
    note_raw = alloc.get("note")
    note_str = str(note_raw) if note_raw else "(no note)"
    return (
        f"  #{index}   ${amount:.2f}   {category:<16}{tags_str:<16}{note_str}"
    )


def _format_transaction_allocations(data: dict[str, object]) -> str:
    """Format a transaction detail response with allocations for display."""
    tx_id = str(data.get("id") or "")
    date_str = str(
        data.get("posted_date") or data.get("authorized_date") or "N/A"
    )
    merchant = str(data.get("name") or data.get("merchant_name") or "Unknown")
    raw_amount = data.get("amount")
    tx_amount = round(
        float(raw_amount) if isinstance(raw_amount, (int, float)) else 0.0, 2
    )
    raw_allocs = data.get("allocations")
    allocations: list[dict[str, object]] = (
        raw_allocs if isinstance(raw_allocs, list) else []
    )

    lines: list[str] = [
        f"Transaction: {tx_id}",
        f"  Date:     {date_str}",
        f"  Merchant: {merchant}",
        f"  Amount:   ${tx_amount:.2f}",
        "",
        f"Allocations ({len(allocations)}):",
    ]
    for i, alloc in enumerate(allocations, 1):
        lines.append(_format_alloc_row(i, alloc))

    lines.append(f"  {_ALLOC_SEPARATOR}")

    alloc_total = round(sum(_alloc_amount(a) for a in allocations), 2)
    diff = round(tx_amount - alloc_total, 2)
    if diff == 0.0:
        balance = "\u2713 Balanced"
    else:
        balance = f"\u26a0 Unbalanced (diff: ${abs(diff):.2f})"

    lines.append(f"  Total: ${alloc_total:.2f}   {balance}")
    return "\n".join(lines)


@allocations_app.command("show")
def allocations_show(
    transaction_id: Annotated[
        str, typer.Argument(help="Plaid transaction ID to inspect.")
    ],
) -> None:
    """Show the current allocation state for a transaction."""
    base_url, secret = _allocations_api_config()
    with httpx.Client() as client:
        response = client.get(
            f"{base_url}/transactions/{transaction_id}",
            headers={"Authorization": f"Bearer {secret}"},
        )

    if response.status_code == http.HTTPStatus.UNAUTHORIZED:
        typer.echo("Authentication failed — check CLAW_API_SECRET")
        raise SystemExit(1)
    if response.status_code == http.HTTPStatus.NOT_FOUND:
        typer.echo(f"Transaction not found: {transaction_id}")
        raise SystemExit(1)
    if response.status_code != http.HTTPStatus.OK:
        typer.echo(
            f"allocations show: HTTP {response.status_code}: {response.text}"
        )
        raise SystemExit(1)

    typer.echo(_format_transaction_allocations(response.json()))


@allocations_app.command("set")
def allocations_set(
    transaction_id: Annotated[
        str, typer.Argument(help="Plaid transaction ID to update.")
    ],
    file: Annotated[
        str,
        typer.Option(
            "--file",
            help=(
                "Path to a JSON file containing the allocation array. "
                "Pass - to read from stdin."
            ),
        ),
    ],
) -> None:
    """Replace all allocations for a transaction from a JSON file."""
    if file == "-":
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(file).read_text(encoding="utf-8")
        except OSError as exc:
            typer.echo(f"allocations set: could not read file: {exc}")
            raise SystemExit(1) from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        typer.echo(f"allocations set: invalid JSON: {exc}")
        raise SystemExit(1) from exc

    base_url, secret = _allocations_api_config()
    with httpx.Client() as client:
        response = client.put(
            f"{base_url}/transactions/{transaction_id}/allocations",
            json=body,
            headers={"Authorization": f"Bearer {secret}"},
        )

    if response.status_code == http.HTTPStatus.OK:
        typer.echo(_format_transaction_allocations(response.json()))
        return

    if response.status_code == http.HTTPStatus.UNAUTHORIZED:
        typer.echo("Authentication failed — check CLAW_API_SECRET")
    elif response.status_code == http.HTTPStatus.NOT_FOUND:
        typer.echo(f"Transaction not found: {transaction_id}")
    elif response.status_code == http.HTTPStatus.CONFLICT:
        detail = response.json().get("detail", {})
        msg = (
            detail.get("message") if isinstance(detail, dict) else str(detail)
        )
        typer.echo(f"allocations set: {msg}")
    elif response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY:
        detail = response.json().get("detail")
        if isinstance(detail, dict) and "transaction_amount" in detail:
            typer.echo(
                f"allocations set: allocation amounts do not balance\n"
                f"  transaction_amount: ${detail['transaction_amount']:.2f}\n"
                f"  allocation_total:   ${detail['allocation_total']:.2f}\n"
                f"  difference:         ${detail['difference']:.2f}"
            )
        else:
            typer.echo(f"allocations set: validation error: {detail}")
    else:
        typer.echo(
            f"allocations set: HTTP {response.status_code}: {response.text}"
        )

    raise SystemExit(1)


def main() -> None:
    """Run the CLI."""
    app()


if __name__ == "__main__":
    main()
