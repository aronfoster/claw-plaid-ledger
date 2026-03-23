"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

import fastapi
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from claw_plaid_ledger.config import (
    Config,
    ConfigError,
    OpenClawConfig,
    load_config,
    load_merged_env,
)
from claw_plaid_ledger.db import (
    AccountLabelRow,
    AnnotationRow,
    LedgerErrorQuery,
    SpendQuery,
    SpendTrendsQuery,
    TransactionQuery,
    get_account,
    get_all_accounts,
    get_all_sync_state,
    get_annotation,
    get_distinct_categories,
    get_distinct_tags,
    get_transaction,
    query_ledger_errors,
    query_spend,
    query_spend_trends,
    query_transactions,
    upsert_account_label,
    upsert_annotation,
)
from claw_plaid_ledger.items_config import ItemConfig, load_items_config
from claw_plaid_ledger.logging_utils import (
    LedgerDbHandler,
    get_correlation_id,
    redact_webhook_body,
    reset_correlation_id,
    set_correlation_id,
)
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.middleware.correlation import CorrelationIdMiddleware
from claw_plaid_ledger.middleware.ip_allowlist import (
    WebhookIPAllowlistMiddleware,
)
from claw_plaid_ledger.notifier import notify_openclaw
from claw_plaid_ledger.plaid_adapter import PlaidClientAdapter
from claw_plaid_ledger.sync_engine import run_sync
from claw_plaid_ledger.webhook_auth import verify_plaid_signature

logger = logging.getLogger(__name__)

_SYNC_UPDATES_AVAILABLE = "SYNC_UPDATES_AVAILABLE"


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


class ErrorListQuery(BaseModel):
    """Validated query parameters for the GET /errors endpoint."""

    hours: int = Field(default=24, ge=1)
    min_severity: Literal["WARNING", "ERROR"] | None = None
    limit: int = Query(default=100, le=500)
    offset: int = 0


@app.get("/errors", dependencies=[Depends(require_bearer_token)])
def list_errors(
    params: Annotated[ErrorListQuery, Depends()],
) -> dict[str, object]:
    """Return recent ledger warnings and errors."""
    config = load_config()
    query = LedgerErrorQuery(
        hours=params.hours,
        min_severity=params.min_severity,
        limit=params.limit,
        offset=params.offset,
    )
    since = datetime.now(UTC) - timedelta(hours=params.hours)
    with sqlite3.connect(config.db_path) as connection:
        rows, total = query_ledger_errors(connection, query)
    return {
        "errors": rows,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
        "since": since.isoformat(),
    }


_SpendRange = Literal[
    "last_month", "this_month", "last_30_days", "last_7_days"
]


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
    date_range: Annotated[_SpendRange | None, Query(alias="range")] = None,
) -> dict[str, object]:
    """
    List transactions with optional filtering and pagination.

    Pass ``tags`` multiple times to require all listed tags (AND semantics).
    Set ``search_notes=true`` together with ``keyword`` to also match the
    annotation note field in addition to ``name`` and ``merchant_name``.

    ``range`` is an optional shorthand for common date windows
    (``this_month``, ``last_month``, ``last_30_days``, ``last_7_days``).
    Explicit ``start_date`` / ``end_date`` override the range-derived dates
    when both are provided together.
    """
    resolved_tags: tuple[str, ...] = tuple(tags) if tags else ()
    start_date = params.start_date
    end_date = params.end_date
    if date_range is not None:
        parsed_start = date.fromisoformat(start_date) if start_date else None
        parsed_end = date.fromisoformat(end_date) if end_date else None
        resolved_start, resolved_end = _resolve_spend_dates(
            date_range, parsed_start, parsed_end
        )
        start_date = resolved_start.isoformat()
        end_date = resolved_end.isoformat()
    config = load_config()
    query = TransactionQuery(
        start_date=start_date,
        end_date=end_date,
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
    FBT001/FBT002 constraints.  ``start_date`` and ``end_date`` are optional
    because they may be derived from the ``range`` shorthand parameter.
    """

    start_date: date | None = None
    end_date: date | None = None
    owner: str | None = None
    # bool | None avoids FBT001/FBT002; None is treated as False (conservative
    # default: exclude pending transactions unless caller opts in).
    include_pending: bool | None = None
    view: Literal["canonical", "raw"] = "canonical"
    account_id: str | None = None
    category: str | None = None
    tag: str | None = None


def _today() -> date:
    """Return the current local date. Extracted for testability."""
    return datetime.now(tz=UTC).astimezone().date()


def _resolve_spend_dates(
    date_range: _SpendRange | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    """
    Resolve ``start_date`` and ``end_date`` from a range shorthand.

    If *date_range* is supplied, derive both dates from it using server local
    time, then apply any explicit ``start_date``/``end_date`` overrides.
    If *date_range* is absent, both ``start_date`` and ``end_date`` must be
    present; otherwise raises HTTP 422.
    """
    if date_range is not None:
        today = _today()
        if date_range == "this_month":
            derived_start: date = today.replace(day=1)
            derived_end: date = today
        elif date_range == "last_month":
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            derived_start = last_month_end.replace(day=1)
            derived_end = last_month_end
        elif date_range == "last_30_days":
            derived_start = today - timedelta(days=30)
            derived_end = today
        else:  # last_7_days
            derived_start = today - timedelta(days=7)
            derived_end = today
        resolved_start = (
            start_date if start_date is not None else derived_start
        )
        resolved_end = end_date if end_date is not None else derived_end
        return resolved_start, resolved_end

    if start_date is None or end_date is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Provide either 'range' or both 'start_date' and 'end_date'."
            ),
        )
    return start_date, end_date


@app.get("/spend", dependencies=[Depends(require_bearer_token)])
def get_spend(
    params: Annotated[SpendListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
    date_range: Annotated[_SpendRange | None, Query(alias="range")] = None,
) -> dict[str, object]:
    """
    Return aggregate spend totals for a date window with optional filters.

    Sums transaction amounts over the inclusive date window.  Positive amounts
    are debits (money leaving the account); negative amounts are credits —
    the sum is returned as-is per Plaid conventions.  Pass ``tags`` multiple
    times to require all listed tags (AND semantics).

    ``range`` is an optional shorthand for common date windows
    (``this_month``, ``last_month``, ``last_30_days``, ``last_7_days``).
    Explicit ``start_date`` / ``end_date`` override the range-derived dates
    when both are provided together.  If ``range`` is absent, both
    ``start_date`` and ``end_date`` are required.
    """
    resolved_start, resolved_end = _resolve_spend_dates(
        date_range, params.start_date, params.end_date
    )
    resolved_tags: list[str] = tags or []
    include_pending = params.include_pending is True
    config = load_config()
    spend_query = SpendQuery(
        start_date=resolved_start.isoformat(),
        end_date=resolved_end.isoformat(),
        owner=params.owner,
        tags=tuple(resolved_tags),
        include_pending=include_pending,
        canonical_only=params.view == "canonical",
        account_id=params.account_id,
        category=params.category,
        tag=params.tag,
    )
    with sqlite3.connect(config.db_path) as connection:
        total_spend, transaction_count = query_spend(connection, spend_query)
    return {
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "total_spend": total_spend,
        "transaction_count": transaction_count,
        "includes_pending": include_pending,
        "filters": {
            "owner": params.owner,
            "tags": resolved_tags,
            "account_id": params.account_id,
            "category": params.category,
            "tag": params.tag,
        },
    }


class SpendTrendsListQuery(BaseModel):
    """Scalar query parameters for GET /spend/trends."""

    months: int = Field(default=6, ge=1)
    owner: str | None = None
    # bool | None avoids FBT001/FBT002; None is treated as False (conservative
    # default: exclude pending transactions unless caller opts in).
    include_pending: bool | None = None
    view: Literal["canonical", "raw"] = "canonical"
    account_id: str | None = None
    category: str | None = None
    tag: str | None = None


@app.get("/spend/trends", dependencies=[Depends(require_bearer_token)])
def get_spend_trends(
    params: Annotated[SpendTrendsListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
) -> list[dict[str, object]]:
    """
    Return spend aggregated by calendar month for a lookback window.

    Returns exactly ``months`` buckets ordered oldest → newest. The
    current (in-progress) calendar month is flagged ``partial: true``
    so callers know not to compare it directly against complete months.
    Months with no qualifying transactions appear as zero-filled buckets.

    Supports the same filters as ``GET /spend`` (``owner``, ``tags``,
    ``category``, ``tag``, ``account_id``, ``view``,
    ``include_pending``) for direct comparability.
    """
    resolved_tags: list[str] = tags or []
    include_pending = params.include_pending is True
    config = load_config()
    trends_query = SpendTrendsQuery(
        months=params.months,
        owner=params.owner,
        tags=tuple(resolved_tags),
        include_pending=include_pending,
        canonical_only=params.view == "canonical",
        account_id=params.account_id,
        category=params.category,
        tag=params.tag,
    )
    with sqlite3.connect(config.db_path) as connection:
        return query_spend_trends(connection, trends_query, _today())


def _fetch_transaction_with_annotation(
    connection: sqlite3.Connection,
    transaction_id: str,
) -> dict[str, object] | None:
    """Return a transaction merged with its annotation, or None if absent."""
    transaction = get_transaction(connection, transaction_id)
    if transaction is None:
        return None

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


@app.get(
    "/transactions/{transaction_id}",
    dependencies=[Depends(require_bearer_token)],
)
def get_transaction_detail(transaction_id: str) -> dict[str, object]:
    """Return one transaction with optional merged annotation."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        result = _fetch_transaction_with_annotation(connection, transaction_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


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
) -> dict[str, object]:
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
        result = _fetch_transaction_with_annotation(connection, transaction_id)
    if result is None:
        # Should not happen: we verified the transaction exists above.
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


@app.get("/categories", dependencies=[Depends(require_bearer_token)])
def get_categories() -> dict[str, object]:
    """Return distinct non-null category values from annotations, sorted."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        categories = get_distinct_categories(connection)
    return {"categories": categories}


@app.get("/tags", dependencies=[Depends(require_bearer_token)])
def get_tags() -> dict[str, object]:
    """Return distinct tag values unnested from all annotations, sorted."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        tags = get_distinct_tags(connection)
    return {"tags": tags}


@app.get("/accounts", dependencies=[Depends(require_bearer_token)])
def list_accounts() -> dict[str, object]:
    """Return all known accounts joined with any available label data."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        accounts = get_all_accounts(connection)
    return {"accounts": accounts}


class AccountLabelRequest(BaseModel):
    """Request body for PUT /accounts/{account_id}."""

    label: str | None = None
    description: str | None = None


@app.put(
    "/accounts/{account_id}",
    dependencies=[Depends(require_bearer_token)],
)
def put_account_label(
    account_id: str, body: AccountLabelRequest
) -> dict[str, object]:
    """Upsert label data for an account and return the full account record."""
    config = load_config()
    with sqlite3.connect(config.db_path) as connection:
        if get_account(connection, account_id) is None:
            raise HTTPException(status_code=404, detail="Account not found")
        now = datetime.now(tz=UTC).isoformat()
        row = AccountLabelRow(
            plaid_account_id=account_id,
            label=body.label,
            description=body.description,
            created_at=now,
            updated_at=now,
        )
        upsert_account_label(connection, row)
        result = get_account(connection, account_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return result


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
                    token = load_merged_env().get(cfg.access_token_env)
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
        logger.warning("Unrecognized Plaid webhook type: %s", webhook_type)

    return {"status": "ok"}
