"""Webhook route, background sync, and lifespan for claw-plaid-ledger."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import fastapi
from fastapi import BackgroundTasks, Request

from claw_plaid_ledger.config import (
    ConfigError,
    OpenClawConfig,
    load_config,
    load_merged_env,
)
from claw_plaid_ledger.db import get_all_sync_state
from claw_plaid_ledger.items_config import ItemConfig, load_items_config
from claw_plaid_ledger.logging_utils import (
    LedgerDbHandler,
    get_correlation_id,
    redact_webhook_body,
    reset_correlation_id,
    set_correlation_id,
)
from claw_plaid_ledger.notifier import notify_openclaw
from claw_plaid_ledger.plaid_adapter import PlaidClientAdapter
from claw_plaid_ledger.sync_engine import run_sync
from claw_plaid_ledger.webhook_auth import verify_plaid_signature

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from claw_plaid_ledger.config import Config

logger = logging.getLogger(__name__)

_SYNC_UPDATES_AVAILABLE = "SYNC_UPDATES_AVAILABLE"
_WEBHOOK_PATH = "/webhooks/plaid"

# Poll interval for the scheduled sync loop.  This is the check frequency,
# not the fallback window (configured via CLAW_SCHEDULED_SYNC_FALLBACK_HOURS).
# 60 minutes is a reasonable balance between responsiveness and overhead.
_SCHEDULED_SYNC_POLL_INTERVAL_SECONDS = 3600

router = fastapi.APIRouter()


async def _background_sync(
    *,
    access_token: str | None = None,
    item_id: str | None = None,
    owner: str | None = None,
    sync_run_id: str | None = None,
) -> None:
    """
    Load config, build adapter, and run sync; log errors without raising.

    When ``access_token`` is ``None`` the function falls back to loading
    ``PLAID_ACCESS_TOKEN`` from config (existing single-item behavior).
    When ``item_id`` is ``None`` the function falls back to ``config.item_id``.
    When ``sync_run_id`` is provided it is used as the correlation ID for all
    log lines emitted during this sync run; otherwise a new ID is generated.
    """
    if sync_run_id is None:
        sync_run_id = "sync-" + uuid.uuid4().hex[:8]
    ctx_token = set_correlation_id(sync_run_id)
    try:
        # When an access token is injected (multi-item path) we only require
        # the Plaid client credentials, not PLAID_ACCESS_TOKEN.
        require_plaid = access_token is None
        config = load_config(
            require_plaid=require_plaid,
            require_plaid_client=not require_plaid,
        )
        resolved_token = (
            access_token
            if access_token is not None
            else config.plaid_access_token
        )
        if not resolved_token:
            logger.error(
                "Background sync aborted: PLAID_ACCESS_TOKEN not set"
                " sync_run_id=%s",
                sync_run_id,
            )
            return
        resolved_item_id = item_id if item_id is not None else config.item_id
        adapter = PlaidClientAdapter.from_config(config)
        logger.info(
            "Background sync starting item_id=%s sync_run_id=%s",
            resolved_item_id,
            sync_run_id,
        )
        summary = run_sync(
            db_path=config.db_path,
            adapter=adapter,
            access_token=resolved_token,
            item_id=resolved_item_id,
            owner=owner,
        )
        logger.info(
            "Background sync completed item_id=%s accounts=%d added=%d"
            " modified=%d removed=%d sync_run_id=%s",
            resolved_item_id,
            summary.accounts,
            summary.added,
            summary.modified,
            summary.removed,
            sync_run_id,
        )
        if summary.added + summary.modified + summary.removed > 0:
            notify_openclaw(
                summary,
                OpenClawConfig(
                    url=config.openclaw_hooks_url,
                    token=config.openclaw_hooks_token,
                    agent=config.openclaw_hooks_agent,
                    wake_mode=config.openclaw_hooks_wake_mode,
                ),
            )
        else:
            logger.warning(
                "Background sync completed with zero transaction changes;"
                " verify webhook payload and Plaid item state"
                " sync_run_id=%s",
                sync_run_id,
            )
    except Exception:
        logger.exception("Background sync failed sync_run_id=%s", sync_run_id)
    finally:
        reset_correlation_id(ctx_token)


def _load_sync_states(
    db_path: Path,
) -> dict[str, str | None] | None:
    """Load sync_state rows from the DB, returning None on failure."""
    try:
        with sqlite3.connect(db_path) as conn:
            return {
                row.item_id: row.last_synced_at
                for row in get_all_sync_state(conn)
            }
    except Exception:
        logger.exception(
            "scheduled-sync: failed to read sync_state from DB; aborting pass"
        )
        return None


def _hours_since_sync(
    last_synced_str: str | None,
    now: datetime,
    fallback_window: timedelta,
) -> tuple[bool, float | None]:
    """Return (overdue, hours_since) for a given last_synced_at string."""
    if last_synced_str is None:
        return True, None
    last_synced_dt = datetime.fromisoformat(last_synced_str)
    elapsed = now - last_synced_dt
    hours_since = elapsed.total_seconds() / 3600
    return elapsed >= fallback_window, hours_since


async def _sync_item_if_overdue(
    item_cfg: ItemConfig,
    last_synced_str: str | None,
    now: datetime,
    fallback_window: timedelta,
) -> None:
    """Sync one configured item if it is overdue; log and skip if recent."""
    overdue, hours_since = _hours_since_sync(
        last_synced_str, now, fallback_window
    )
    hours_desc = f"{hours_since:.1f}h" if hours_since is not None else "never"
    if not overdue:
        logger.debug(
            "scheduled-sync: item %s is recent (%s since last sync); skipping",
            item_cfg.id,
            hours_desc,
        )
        return
    logger.info(
        "scheduled-sync: item %s overdue (%s since last sync);"
        " triggering fallback sync",
        item_cfg.id,
        hours_desc,
    )
    token = load_merged_env().get(item_cfg.access_token_env)
    if not token:
        logger.error(
            "scheduled-sync: item %s: env var %s not set; skipping",
            item_cfg.id,
            item_cfg.access_token_env,
        )
        return
    await _background_sync(
        access_token=token,
        item_id=item_cfg.id,
        owner=item_cfg.owner,
    )


async def _check_multi_item(
    items: list[ItemConfig],
    sync_states: dict[str, str | None],
    now: datetime,
    fallback_window: timedelta,
) -> None:
    """Check and sync overdue items from a multi-item items.toml list."""
    for item_cfg in items:
        try:
            await _sync_item_if_overdue(
                item_cfg,
                sync_states.get(item_cfg.id),
                now,
                fallback_window,
            )
        except Exception:
            logger.exception(
                "scheduled-sync: error checking item %s; continuing",
                item_cfg.id,
            )


async def _check_and_sync_overdue_items(config: Config) -> None:
    """
    Sync any configured item whose last sync is older than the fallback window.

    Iterates items from items.toml (single-item env-var fallback when absent).
    Items overdue or never synced get a _background_sync() call.  One item
    failing does not prevent the others from being checked.
    """
    fallback_window = timedelta(hours=config.scheduled_sync_fallback_hours)
    now = datetime.now(tz=UTC)

    try:
        items = load_items_config()
    except (OSError, ValueError):
        logger.warning(
            "scheduled-sync: could not load items.toml;"
            " using single-item fallback"
        )
        items = []

    sync_states = _load_sync_states(config.db_path)
    if sync_states is None:
        return

    if items:
        await _check_multi_item(items, sync_states, now, fallback_window)
    else:
        # Single-item fallback: check the configured default item_id.
        # _background_sync() with no args loads PLAID_ACCESS_TOKEN from config.
        try:
            overdue, hours_since = _hours_since_sync(
                sync_states.get(config.item_id), now, fallback_window
            )
            hours_desc = (
                f"{hours_since:.1f}h" if hours_since is not None else "never"
            )
            if overdue:
                logger.info(
                    "scheduled-sync: item %s overdue (%s since last sync);"
                    " triggering fallback sync",
                    config.item_id,
                    hours_desc,
                )
                await _background_sync()
            else:
                logger.debug(
                    "scheduled-sync: item %s is recent"
                    " (%s since last sync); skipping",
                    config.item_id,
                    hours_desc,
                )
        except Exception:
            logger.exception(
                "scheduled-sync: error checking single-item fallback;"
                " aborting pass"
            )


async def _scheduled_sync_loop(config: Config) -> None:
    """Run forever, waking every 60 minutes to check for overdue items."""
    while True:
        await asyncio.sleep(_SCHEDULED_SYNC_POLL_INTERVAL_SECONDS)
        await _check_and_sync_overdue_items(config)


@asynccontextmanager
async def lifespan(
    _app: fastapi.FastAPI,
) -> AsyncGenerator[None, None]:
    """Start and cleanly stop the scheduled sync background task."""
    task: asyncio.Task[None] | None = None
    db_handler: LedgerDbHandler | None = None
    try:
        cfg = load_config()
    except ConfigError:
        logger.warning(
            "lifespan: could not load config; scheduled sync will not start"
        )
        cfg = None

    if cfg is not None:
        db_handler = LedgerDbHandler(cfg.db_path)
        logging.getLogger().addHandler(db_handler)

        if cfg.scheduled_sync_enabled:
            logger.info(
                "lifespan: starting scheduled sync loop (fallback_hours=%d)",
                cfg.scheduled_sync_fallback_hours,
            )
            task = asyncio.create_task(_scheduled_sync_loop(cfg))

    yield

    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info("lifespan: scheduled sync loop stopped")

    if db_handler is not None:
        logging.getLogger().removeHandler(db_handler)


def _enqueue_sync_updates(
    payload_item_id: str | None,
    sync_run_id: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Route and enqueue a SYNC_UPDATES_AVAILABLE background sync."""
    if not payload_item_id:
        logger.warning(
            "SYNC_UPDATES_AVAILABLE: no item_id in payload;"
            " using single-item fallback sync_run_id=%s",
            sync_run_id,
        )
        background_tasks.add_task(_background_sync, sync_run_id=sync_run_id)
        return

    try:
        items = load_items_config()
    except (OSError, ValueError):
        logger.warning(
            "Could not load items.toml; falling back to PLAID_ACCESS_TOKEN"
        )
        items = []

    if items:
        cfg = next((c for c in items if c.id == payload_item_id), None)
        if cfg is not None:
            token = load_merged_env().get(cfg.access_token_env)
            if token:
                logger.info(
                    "Enqueuing background sync for item_id=%s"
                    " webhook_type=%s sync_run_id=%s",
                    payload_item_id,
                    _SYNC_UPDATES_AVAILABLE,
                    sync_run_id,
                )
                background_tasks.add_task(
                    _background_sync,
                    access_token=token,
                    item_id=cfg.id,
                    owner=cfg.owner,
                    sync_run_id=sync_run_id,
                )
                return
            logger.error(
                "item_id %s: env var %s not set;"
                " falling back to PLAID_ACCESS_TOKEN",
                payload_item_id,
                cfg.access_token_env,
            )
        else:
            logger.warning(
                "item_id %s not found in items.toml;"
                " falling back to PLAID_ACCESS_TOKEN",
                payload_item_id,
            )

    logger.info(
        "Enqueuing background sync for webhook_type=%s sync_run_id=%s",
        _SYNC_UPDATES_AVAILABLE,
        sync_run_id,
    )
    background_tasks.add_task(_background_sync, sync_run_id=sync_run_id)


@router.post("/webhooks/plaid")
async def webhook_plaid(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Handle Plaid webhook events."""
    body = await request.body()
    headers = dict(request.headers)

    if not verify_plaid_signature(body, headers):
        logger.error("Plaid webhook signature verification failed")
        raise fastapi.HTTPException(
            status_code=400, detail="Invalid signature"
        )

    try:
        payload = json.loads(body)
    except ValueError:
        logger.exception(
            "Plaid webhook body is not valid JSON; body_preview=%r",
            body[:200],
        )
        raise fastapi.HTTPException(
            status_code=400, detail="Invalid JSON body"
        ) from None
    webhook_type = payload.get("webhook_type", "")
    webhook_code = payload.get("webhook_code", "")
    logger.info(
        "Plaid webhook received webhook_type=%s webhook_code=%s",
        webhook_type,
        webhook_code,
    )
    logger.debug(
        "webhook payload (redacted): %s", redact_webhook_body(payload)
    )

    # Derive a sync_run_id from the current request_id so the resulting sync
    # run is traceable back to this webhook event in the logs.
    request_id = get_correlation_id()
    sync_run_id = (
        "sync-" + request_id[4:]
        if request_id.startswith("req-")
        else "sync-" + uuid.uuid4().hex[:8]
    )

    if webhook_code == _SYNC_UPDATES_AVAILABLE:
        _enqueue_sync_updates(
            payload_item_id=payload.get("item_id"),
            sync_run_id=sync_run_id,
            background_tasks=background_tasks,
        )
    else:
        logger.warning(
            "Unrecognized Plaid webhook_code=%s"
            " webhook_type=%s; no sync triggered",
            webhook_code,
            webhook_type,
        )

    return {"status": "ok"}
