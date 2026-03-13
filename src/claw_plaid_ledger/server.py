"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from pathlib import Path

    from starlette.responses import Response

import fastapi
import fastapi.responses
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from claw_plaid_ledger.config import (
    Config,
    ConfigError,
    OpenClawConfig,
    load_api_secret,
    load_config,
)
from claw_plaid_ledger.db import (
    AnnotationRow,
    SpendQuery,
    TransactionQuery,
    get_all_sync_state,
    get_annotation,
    get_transaction,
    query_spend,
    query_transactions,
    upsert_annotation,
)
from claw_plaid_ledger.items_config import ItemConfig, load_items_config
from claw_plaid_ledger.logging_utils import (
    get_correlation_id,
    redact_webhook_body,
    reset_correlation_id,
    set_correlation_id,
)
from claw_plaid_ledger.notifier import notify_openclaw
from claw_plaid_ledger.plaid_adapter import PlaidClientAdapter
from claw_plaid_ledger.sync_engine import run_sync
from claw_plaid_ledger.webhook_auth import verify_plaid_signature

_bearer_scheme = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)

_SYNC_UPDATES_AVAILABLE = "SYNC_UPDATES_AVAILABLE"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Attach a unique request_id to every HTTP request.

    - Generates ``request_id = "req-" + uuid4().hex[:8]`` per request.
    - Stores it in a ``ContextVar`` so all code in the request call stack
      picks it up via the ``CorrelationIdFilter`` without explicit threading.
    - Emits INFO logs at request start and end.
    - Adds ``X-Request-Id`` to the response headers.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Generate request_id, set context var, log, add header."""
        request_id = "req-" + uuid.uuid4().hex[:8]
        token = set_correlation_id(request_id)
        logger.info(
            "request_start method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            request_id,
        )
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)
        logger.info(
            "request_end method=%s path=%s status=%d request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            request_id,
        )
        response.headers["X-Request-Id"] = request_id
        return response


_WEBHOOK_PATH = "/webhooks/plaid"


def _resolve_client_ip(
    request: Request,
    trusted_proxies: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """
    Return the real client IP, honouring X-Forwarded-For from proxies.

    If the direct connection address is in *trusted_proxies*, use the
    leftmost address in the ``X-Forwarded-For`` header as the real client
    IP.  Otherwise the direct connection address is returned as-is.
    """
    direct_ip_str = request.client.host if request.client else "127.0.0.1"
    try:
        direct_ip = ipaddress.ip_address(direct_ip_str)
    except ValueError:
        direct_ip = ipaddress.IPv4Address("127.0.0.1")

    if direct_ip not in trusted_proxies:
        return direct_ip

    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        leftmost = xff.split(",")[0].strip()
        try:
            return ipaddress.ip_address(leftmost)
        except ValueError:
            pass

    return direct_ip


def _ip_in_allowlist(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Return True if *ip* falls within any network in *networks*."""
    return any(ip in net for net in networks)


class WebhookIPAllowlistMiddleware(BaseHTTPMiddleware):
    """
    Block non-allowlisted IPs from POST /webhooks/plaid.

    Only active when ``CLAW_WEBHOOK_ALLOWED_IPS`` is configured in the
    environment.  All other routes are unaffected.

    IP resolution order:
    1. If the direct connection IP is in ``CLAW_TRUSTED_PROXIES`` (default:
       127.0.0.1), take the leftmost address from ``X-Forwarded-For`` as
       the real client IP.
    2. Otherwise use the direct connection IP.

    Blocked requests receive HTTP 403 ``{"detail": "forbidden"}`` and a
    WARNING-level log line with the resolved IP and ``request_id``.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Enforce the IP allowlist for POST /webhooks/plaid."""
        if request.method != "POST" or request.url.path != _WEBHOOK_PATH:
            return await call_next(request)

        try:
            config = load_config()
        except ConfigError:
            # Config unavailable — fail open so the existing auth layers
            # remain the last line of defence rather than a broken startup.
            return await call_next(request)

        if not config.webhook_allowed_ips:
            return await call_next(request)

        client_ip = _resolve_client_ip(request, config.trusted_proxies)
        if _ip_in_allowlist(client_ip, config.webhook_allowed_ips):
            return await call_next(request)

        request_id = get_correlation_id()
        logger.warning(
            "webhook IP blocked ip=%s request_id=%s",
            client_ip,
            request_id,
        )
        return fastapi.responses.JSONResponse(
            status_code=403,
            content={"detail": "forbidden"},
        )


def require_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> None:
    """Enforce Authorization: Bearer <token> using CLAW_API_SECRET."""
    api_secret = load_api_secret()
    if not api_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if credentials is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not secrets.compare_digest(credentials.credentials, api_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


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
    token = os.environ.get(item_cfg.access_token_env)
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


# Poll interval for the scheduled sync loop.  This is the check frequency,
# not the fallback window (configured via CLAW_SCHEDULED_SYNC_FALLBACK_HOURS).
# 60 minutes is a reasonable balance between responsiveness and overhead.
_SCHEDULED_SYNC_POLL_INTERVAL_SECONDS = 3600


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
    try:
        cfg = load_config()
    except ConfigError:
        logger.warning(
            "lifespan: could not load config; scheduled sync will not start"
        )
        cfg = None

    if cfg is not None and cfg.scheduled_sync_enabled:
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


app = fastapi.FastAPI(title="claw-plaid-ledger", lifespan=lifespan)
# Middleware is applied in reverse registration order (last added = outermost).
# CorrelationIdMiddleware must be outermost so request_id is set before the
# allowlist middleware logs its WARNING.
app.add_middleware(WebhookIPAllowlistMiddleware)
app.add_middleware(CorrelationIdMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    """Return service liveness status."""
    return {"status": "ok"}


class TransactionListQuery(BaseModel):
    """Validated query parameters for the transactions list endpoint."""

    start_date: str | None = None
    end_date: str | None = None
    account_id: str | None = None
    pending: bool | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    keyword: str | None = None
    view: Literal["canonical", "raw"] = "canonical"
    limit: int = Query(default=100, le=500)
    offset: int = 0
    # bool | None avoids FBT001/FBT002; None is treated as False (default:
    # keyword searches do not include annotation notes unless opted in).
    search_notes: bool | None = None


@app.get("/transactions", dependencies=[Depends(require_bearer_token)])
def list_transactions(
    params: Annotated[TransactionListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
) -> dict[str, object]:
    """
    List transactions with optional filtering and pagination.

    Pass ``tags`` multiple times to require all listed tags (AND semantics).
    Set ``search_notes=true`` together with ``keyword`` to also match the
    annotation note field in addition to ``name`` and ``merchant_name``.
    """
    resolved_tags: tuple[str, ...] = tuple(tags) if tags else ()
    config = load_config()
    query = TransactionQuery(
        start_date=params.start_date,
        end_date=params.end_date,
        account_id=params.account_id,
        pending=params.pending,
        min_amount=params.min_amount,
        max_amount=params.max_amount,
        keyword=params.keyword,
        canonical_only=params.view == "canonical",
        limit=params.limit,
        offset=params.offset,
        tags=resolved_tags,
        search_notes=params.search_notes is True,
    )
    with sqlite3.connect(config.db_path) as connection:
        rows, total = query_transactions(connection, query)
    return {
        "transactions": rows,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


class SpendListQuery(BaseModel):
    """
    Scalar query parameters for GET /spend.

    List-typed params (tags) and bool params (include_pending) are declared
    separately on the endpoint to satisfy FastAPI's multi-value and
    FBT001/FBT002 constraints.
    """

    start_date: date
    end_date: date
    owner: str | None = None
    # bool | None avoids FBT001/FBT002; None is treated as False (conservative
    # default: exclude pending transactions unless caller opts in).
    include_pending: bool | None = None
    view: Literal["canonical", "raw"] = "canonical"


@app.get("/spend", dependencies=[Depends(require_bearer_token)])
def get_spend(
    params: Annotated[SpendListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
) -> dict[str, object]:
    """
    Return aggregate spend totals for a date window with optional filters.

    Sums transaction amounts over the inclusive date window.  Positive amounts
    are debits (money leaving the account); negative amounts are credits —
    the sum is returned as-is per Plaid conventions.  Pass ``tags`` multiple
    times to require all listed tags (AND semantics).
    """
    resolved_tags: list[str] = tags or []
    include_pending = params.include_pending is True
    config = load_config()
    spend_query = SpendQuery(
        start_date=params.start_date.isoformat(),
        end_date=params.end_date.isoformat(),
        owner=params.owner,
        tags=tuple(resolved_tags),
        include_pending=include_pending,
        canonical_only=params.view == "canonical",
    )
    with sqlite3.connect(config.db_path) as connection:
        total_spend, transaction_count = query_spend(connection, spend_query)
    return {
        "start_date": params.start_date.isoformat(),
        "end_date": params.end_date.isoformat(),
        "total_spend": total_spend,
        "transaction_count": transaction_count,
        "includes_pending": include_pending,
        "filters": {
            "owner": params.owner,
            "tags": resolved_tags,
        },
    }


@app.get(
    "/transactions/{transaction_id}",
    dependencies=[Depends(require_bearer_token)],
)
def get_transaction_detail(transaction_id: str) -> dict[str, object]:
    """Return one transaction with optional merged annotation."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        transaction = get_transaction(connection, transaction_id)
        if transaction is None:
            raise HTTPException(
                status_code=404, detail="Transaction not found"
            )

        annotation = get_annotation(connection, transaction_id)

    annotation_payload: dict[str, object] | None = None
    if annotation is not None:
        tags: list[str] | None = None
        if annotation.tags is not None:
            tags_raw = json.loads(annotation.tags)
            tags = [str(tag) for tag in tags_raw]

        annotation_payload = {
            "category": annotation.category,
            "note": annotation.note,
            "tags": tags,
            "updated_at": annotation.updated_at,
        }

    return {**transaction, "annotation": annotation_payload}


class AnnotationRequest(BaseModel):
    """Request body for PUT /annotations/{transaction_id}."""

    category: str | None = None
    note: str | None = None
    tags: list[str] | None = None


@app.put(
    "/annotations/{transaction_id}",
    dependencies=[Depends(require_bearer_token)],
)
def put_annotation(
    transaction_id: str, body: AnnotationRequest
) -> dict[str, str]:
    """Create or fully replace an annotation for a transaction."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        if get_transaction(connection, transaction_id) is None:
            raise HTTPException(
                status_code=404, detail="Transaction not found"
            )
        now = datetime.now(tz=UTC).isoformat()
        existing = get_annotation(connection, transaction_id)
        created_at = existing.created_at if existing is not None else now
        tags_json = json.dumps(body.tags) if body.tags is not None else None
        row = AnnotationRow(
            plaid_transaction_id=transaction_id,
            category=body.category,
            note=body.note,
            tags=tags_json,
            created_at=created_at,
            updated_at=now,
        )
        upsert_annotation(connection, row)
        logger.debug(
            "annotation upserted transaction_id=%s category=%r tags=%s",
            transaction_id,
            body.category,
            body.tags,
        )
    return {"status": "ok"}


@app.post("/webhooks/plaid", dependencies=[Depends(require_bearer_token)])
async def webhook_plaid(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Handle Plaid webhook events."""
    body = await request.body()
    headers = dict(request.headers)

    if not verify_plaid_signature(body, headers):
        logger.error("Plaid webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)
    webhook_type = payload.get("webhook_type", "")
    logger.info("Plaid webhook received webhook_type=%s", webhook_type)
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

    if webhook_type == _SYNC_UPDATES_AVAILABLE:
        payload_item_id: str | None = payload.get("item_id")
        enqueued = False

        if payload_item_id:
            try:
                items = load_items_config()
            except (OSError, ValueError):
                logger.warning(
                    "Could not load items.toml; falling back to"
                    " PLAID_ACCESS_TOKEN"
                )
                items = []

            if items:
                cfg = next((c for c in items if c.id == payload_item_id), None)
                if cfg is not None:
                    token = os.environ.get(cfg.access_token_env)
                    if token:
                        logger.info(
                            "Enqueuing background sync for item_id=%s"
                            " webhook_type=%s sync_run_id=%s",
                            payload_item_id,
                            webhook_type,
                            sync_run_id,
                        )
                        background_tasks.add_task(
                            _background_sync,
                            access_token=token,
                            item_id=cfg.id,
                            owner=cfg.owner,
                            sync_run_id=sync_run_id,
                        )
                        enqueued = True
                    else:
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

        if not enqueued:
            logger.info(
                "Enqueuing background sync for webhook_type=%s sync_run_id=%s",
                webhook_type,
                sync_run_id,
            )
            background_tasks.add_task(
                _background_sync, sync_run_id=sync_run_id
            )
    else:
        logger.warning("Unrecognised Plaid webhook type: %s", webhook_type)

    return {"status": "ok"}
