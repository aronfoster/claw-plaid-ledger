# Sprint 21 — M19: Split server.py into routers

## Sprint goal

Decompose the 1 054-line `server.py` monolith into a proper FastAPI router
structure so that each domain has its own file, the app factory is thin, and
M20 can add an `allocations` router without touching every other concern.

This sprint also resolves **BUG-014** (unknown query parameters silently
ignored) by introducing `_strict_params` as a shared dependency and wiring it
into every parameterised GET endpoint as each router is created.

## Scope

- Pure structural refactor — **zero API behaviour change, zero schema change**,
  no new endpoints.
- BUG-014 (`_strict_params`) is the only new runtime behaviour; it adds HTTP
  422 rejection of unrecognised query parameters to four existing endpoints.
- Quality gate must pass identically before and after **every task**.

## Target module structure

```
src/claw_plaid_ledger/
  server.py               # app factory only: FastAPI(), lifespan import,
                          # middleware registration, router inclusion (~50 lines)
  middleware/
    __init__.py           # empty
    auth.py               # require_bearer_token, _bearer_scheme
    correlation.py        # CorrelationIdMiddleware
    ip_allowlist.py       # WebhookIPAllowlistMiddleware, _resolve_client_ip,
                          # _ip_in_allowlist; imports _WEBHOOK_PATH from
                          # routers.webhooks
  routers/
    __init__.py           # empty
    utils.py              # _SpendRange, _today, _resolve_spend_dates,
                          # _strict_params (BUG-014)
    health.py             # GET /health, GET /errors
    transactions.py       # GET /transactions, GET /transactions/{id},
                          # PUT /annotations/{id}
    spend.py              # GET /spend, GET /spend/trends
    accounts.py           # GET /accounts, PUT /accounts/{id},
                          # GET /categories, GET /tags
    webhooks.py           # POST /webhooks/plaid, _WEBHOOK_PATH,
                          # _background_sync, all scheduling helpers, lifespan
```

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before the
  next starts.
- No test logic may be added, removed, or changed as part of the structural
  moves; only import paths and the new BUG-014 tests are permitted changes.
- All Python changes must pass the full quality gate before commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Mark completed tasks `✅ DONE` in this file before committing.

---

## Task 1: Create the middleware package

### What to do

Create `src/claw_plaid_ledger/middleware/` and move the three cross-cutting
concerns out of `server.py`.

**New files:**

`middleware/__init__.py` — empty.

`middleware/auth.py` — move:
- `_bearer_scheme = HTTPBearer(auto_error=False)` (module-level)
- `require_bearer_token()` function and all its imports

`middleware/correlation.py` — move:
- `CorrelationIdMiddleware` class and all its imports

`middleware/ip_allowlist.py` — move:
- `_WEBHOOK_PATH = "/webhooks/plaid"` — define it **locally here for now**;
  Task 3 will make `routers/webhooks.py` the canonical home and update this
  file to import from there
- `_resolve_client_ip()` function
- `_ip_in_allowlist()` function
- `WebhookIPAllowlistMiddleware` class and all its imports

**Update `server.py`:**

Replace the moved code with imports from the new locations:

```python
from claw_plaid_ledger.middleware.auth import require_bearer_token
from claw_plaid_ledger.middleware.correlation import CorrelationIdMiddleware
from claw_plaid_ledger.middleware.ip_allowlist import WebhookIPAllowlistMiddleware
```

All route handlers, lifespan, and sync helpers remain in `server.py` for now.

**Update test import paths** (import-path changes only, no logic changes):

| Test file | Old import | New import |
|---|---|---|
| `test_server_auth.py` | `from claw_plaid_ledger.server import require_bearer_token` | `from claw_plaid_ledger.middleware.auth import require_bearer_token` |
| `test_server_ip_allowlist.py` | `from claw_plaid_ledger.server import _ip_in_allowlist, _resolve_client_ip` | `from claw_plaid_ledger.middleware.ip_allowlist import _ip_in_allowlist, _resolve_client_ip` |

`test_server_logging.py`, `test_server_sync.py`, and all other test files
import only `app` from `server.py` — no changes required in this task.

### Done when

- Three new middleware modules exist.
- `server.py` no longer defines `require_bearer_token`, `CorrelationIdMiddleware`,
  `WebhookIPAllowlistMiddleware`, `_resolve_client_ip`, or `_ip_in_allowlist`.
- Quality gate passes with the same test count as before this task.

---

## Task 2: Create the routers package scaffold — utils.py and _strict_params

### What to do

Create the routers package and the shared utilities module. This lays the
foundation for Tasks 3–7 and introduces `_strict_params` (BUG-014).

No routes move in this task.

**New files:**

`routers/__init__.py` — empty.

`routers/utils.py` — contains:

```python
"""Shared utilities for claw-plaid-ledger routers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException, Request

_SpendRange = Literal[
    "last_month", "this_month", "last_30_days", "last_7_days"
]


def _today() -> date:
    """Return the current local date. Extracted for testability."""
    return datetime.now(tz=UTC).astimezone().date()


def _resolve_spend_dates(
    date_range: _SpendRange | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    """
    Resolve start_date and end_date from a range shorthand.

    If date_range is supplied, derive both dates from it using server local
    time, then apply any explicit start_date/end_date overrides.
    If date_range is absent, both start_date and end_date must be present;
    otherwise raises HTTP 422.
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
        resolved_start = start_date if start_date is not None else derived_start
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


def _strict_params(allowed: frozenset[str]) -> Callable[[Request], None]:
    """Raise 422 if the request contains any query parameter not in allowed."""

    def _check(request: Request) -> None:
        unknown = sorted(set(request.query_params.keys()) - allowed)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unrecognized query parameters",
                    "unrecognized": unknown,
                    "valid_parameters": sorted(allowed),
                },
            )

    return _check
```

The body of `_resolve_spend_dates` is moved verbatim from `server.py`; the
logic must not change.

**Update `server.py`:**

Remove `_SpendRange`, `_today`, and `_resolve_spend_dates` from `server.py`
and import them back so the remaining route code continues to compile:

```python
from claw_plaid_ledger.routers.utils import (
    _SpendRange,
    _resolve_spend_dates,
    _today,
)
```

### Done when

- `routers/__init__.py` and `routers/utils.py` exist.
- `server.py` no longer defines `_SpendRange`, `_today`, or
  `_resolve_spend_dates`; it imports them from `routers.utils`.
- Quality gate passes with the same test count as before this task.

---

## Task 3: Create routers/webhooks.py

### What to do

Move all background-sync logic, the scheduled-sync infrastructure, the
`lifespan` context manager, and the `POST /webhooks/plaid` route handler into
a dedicated router module. Also promote `_WEBHOOK_PATH` to its canonical home
here and update the middleware import.

**New file: `routers/webhooks.py`**

Move these from `server.py`, in this order:

1. `_SYNC_UPDATES_AVAILABLE = "SYNC_UPDATES_AVAILABLE"`
2. `_WEBHOOK_PATH = "/webhooks/plaid"` — **canonical definition**
3. `_SCHEDULED_SYNC_POLL_INTERVAL_SECONDS = 3600`
4. `_background_sync()` async function (and all its imports)
5. `_load_sync_states()` function
6. `_hours_since_sync()` function
7. `_sync_item_if_overdue()` async function
8. `_check_multi_item()` async function
9. `_check_and_sync_overdue_items()` async function
10. `_scheduled_sync_loop()` async function
11. `lifespan` async context manager
12. The `POST /webhooks/plaid` route handler

Declare an `APIRouter` at module level and register the route on it:

```python
router = APIRouter()

@router.post("/webhooks/plaid", dependencies=[Depends(require_bearer_token)])
async def webhook_plaid(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    ...
```

`require_bearer_token` is imported from `claw_plaid_ledger.middleware.auth`.

**Update `middleware/ip_allowlist.py`:**

Remove the local `_WEBHOOK_PATH = "/webhooks/plaid"` definition and replace
it with:

```python
from claw_plaid_ledger.routers.webhooks import _WEBHOOK_PATH
```

There is no circular import: `middleware/ip_allowlist.py` imports from
`routers/webhooks.py`, and `routers/webhooks.py` imports from
`middleware/auth.py` — no cycle.

**Update `server.py`:**

Remove all moved code. Wire in the new router and pass `lifespan`:

```python
from claw_plaid_ledger.routers import webhooks as webhooks_router_module
from claw_plaid_ledger.routers.webhooks import lifespan

app = fastapi.FastAPI(title="claw-plaid-ledger", lifespan=lifespan)
app.add_middleware(WebhookIPAllowlistMiddleware)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(webhooks_router_module.router)
```

**Update test import paths:**

| Test file | Symbol(s) | New import location |
|---|---|---|
| `test_server_sync.py` | `_background_sync`, `_check_and_sync_overdue_items`, `_scheduled_sync_loop`, `lifespan` | `claw_plaid_ledger.routers.webhooks` |
| `test_server_logging.py` | `_background_sync` | `claw_plaid_ledger.routers.webhooks` |

Both files still import `app` from `claw_plaid_ledger.server` — that import
stays unchanged.

### Done when

- `routers/webhooks.py` exists with the router and all sync/scheduling helpers.
- `middleware/ip_allowlist.py` imports `_WEBHOOK_PATH` from `routers.webhooks`.
- `server.py` no longer defines any sync/scheduling code or the webhook route.
- Quality gate passes with the same test count as before this task.

---

## Task 4: Create routers/health.py — BUG-014 on GET /errors

### What to do

Move the health and errors routes into their own router and wire `_strict_params`
onto `GET /errors`.

**New file: `routers/health.py`**

Move from `server.py`:

- `ErrorListQuery` Pydantic model
- `GET /health` route handler
- `GET /errors` route handler

Declare an `APIRouter` and register both routes on it.

**Wire BUG-014 on `GET /errors`:**

Add `_strict_params` to the `dependencies` list:

```python
from claw_plaid_ledger.routers.utils import _strict_params

_ERRORS_PARAMS = frozenset({"hours", "min_severity", "limit", "offset"})

@router.get(
    "/errors",
    dependencies=[
        Depends(require_bearer_token),
        Depends(_strict_params(_ERRORS_PARAMS)),
    ],
)
def list_errors(...):
    ...
```

`GET /health` accepts no query parameters — do **not** add `_strict_params`
(see BUG-014 notes in BUGS.md: health checks from monitoring tools may append
cache-buster params).

**Update `server.py`:**

Remove the moved code and add:

```python
from claw_plaid_ledger.routers import health as health_router_module
...
app.include_router(health_router_module.router)
```

**Add BUG-014 tests to `test_server_errors.py`:**

Add three new tests (import path changes for existing tests are not required
since they only import `app`):

1. `GET /errors?offest=10` (misspelled) → HTTP 422; response body contains
   `"unrecognized": ["offest"]` and `"valid_parameters": ["hours", "limit",
   "min_severity", "offset"]`.
2. `GET /errors?foo=bar&baz=1` → HTTP 422; `"unrecognized": ["baz", "foo"]`.
3. `GET /errors?hours=1` → HTTP 200 (regression guard; valid parameter
   accepted).

### Done when

- `routers/health.py` exists.
- `GET /errors` rejects unknown query parameters with HTTP 422.
- Three new tests pass.
- Quality gate passes.

---

## Task 5: Create routers/spend.py — BUG-014 on GET /spend and GET /spend/trends

### What to do

Move the spend and spend-trends routes into their own router and wire
`_strict_params` onto both.

**New file: `routers/spend.py`**

Move from `server.py`:

- `SpendListQuery` Pydantic model
- `SpendTrendsListQuery` Pydantic model
- `GET /spend` route handler
- `GET /spend/trends` route handler

Import `_SpendRange`, `_today`, `_resolve_spend_dates` from
`claw_plaid_ledger.routers.utils`.

**Wire BUG-014:**

```python
from claw_plaid_ledger.routers.utils import _strict_params

_SPEND_PARAMS = frozenset({
    "start_date", "end_date", "owner", "tags", "include_pending",
    "view", "account_id", "category", "tag", "range",
})
_SPEND_TRENDS_PARAMS = frozenset({
    "months", "owner", "tags", "include_pending",
    "view", "account_id", "category", "tag",
})
```

Add `Depends(_strict_params(_SPEND_PARAMS))` to `GET /spend`'s `dependencies`
list, and `Depends(_strict_params(_SPEND_TRENDS_PARAMS))` to
`GET /spend/trends`'s `dependencies` list.

**Update `server.py`:**

Remove the moved code and add:

```python
from claw_plaid_ledger.routers import spend as spend_router_module
...
app.include_router(spend_router_module.router)
```

At this point `server.py` no longer needs the `_SpendRange`, `_today`,
`_resolve_spend_dates` re-imports from Task 2 — remove them if the remaining
routes no longer reference them.

**Add BUG-014 tests:**

Add to `test_server_spend.py`:
1. `GET /spend?start_date=2026-01-01&end_date=2026-01-31&typo=1` → HTTP 422
   with `"unrecognized": ["typo"]` and `"valid_parameters"` listing the ten
   valid params.
2. `GET /spend?start_date=2026-01-01&end_date=2026-01-31` → HTTP 200
   (regression guard).

Add to `test_server_spend_trends.py`:
1. `GET /spend/trends?months=3&unknown=x` → HTTP 422 with
   `"unrecognized": ["unknown"]`.
2. `GET /spend/trends?months=3` → HTTP 200 (regression guard).

### Done when

- `routers/spend.py` exists.
- Both spend endpoints reject unknown query parameters with HTTP 422.
- Four new tests pass.
- Quality gate passes.

---

## Task 6: Create routers/transactions.py — BUG-014 on GET /transactions

### What to do

Move the transactions and annotations routes into their own router, wire
`_strict_params` onto `GET /transactions`, and verify that both PUT request
bodies reject unknown fields.

**New file: `routers/transactions.py`**

Move from `server.py`:

- `TransactionListQuery` Pydantic model
- `_fetch_transaction_with_annotation()` helper function
- `GET /transactions` route handler
- `GET /transactions/{transaction_id}` route handler
- `AnnotationRequest` Pydantic model
- `PUT /annotations/{transaction_id}` route handler

Import `_SpendRange`, `_resolve_spend_dates` from
`claw_plaid_ledger.routers.utils`.

**Wire BUG-014 on `GET /transactions` only:**

```python
from claw_plaid_ledger.routers.utils import _strict_params

_TRANSACTIONS_PARAMS = frozenset({
    "start_date", "end_date", "account_id", "pending", "min_amount",
    "max_amount", "keyword", "view", "limit", "offset",
    "search_notes", "tags", "range",
})
```

Add `Depends(_strict_params(_TRANSACTIONS_PARAMS))` to `GET /transactions`'s
`dependencies` list.

`GET /transactions/{id}` and `PUT /annotations/{id}` accept no query
parameters — do **not** add `_strict_params` to them.

**Verify `extra="forbid"` on `AnnotationRequest`:**

Per BUG-014 notes, `PUT /annotations/{id}` should reject unknown request body
fields. Check that `AnnotationRequest` has:

```python
model_config = ConfigDict(extra="forbid")
```

Add it if missing.

**Update `server.py`:**

Remove the moved code and add:

```python
from claw_plaid_ledger.routers import transactions as transactions_router_module
...
app.include_router(transactions_router_module.router)
```

**Add BUG-014 tests to `test_server_transactions.py`:**

1. `GET /transactions?offest=10` → HTTP 422 with `"unrecognized": ["offest"]`
   and `"valid_parameters"` listing the thirteen valid params.
2. `GET /transactions?limit=5` → HTTP 200 (regression guard).

### Done when

- `routers/transactions.py` exists.
- `GET /transactions` rejects unknown query parameters with HTTP 422.
- `AnnotationRequest` has `extra="forbid"`.
- Two new tests pass.
- Quality gate passes.

---

## Task 7: Create routers/accounts.py — finish thinning server.py

### What to do

Move the accounts, categories, and tags routes into their own router. After
this task `server.py` must be ≤50 lines and contain only the app factory.

**New file: `routers/accounts.py`**

Move from `server.py`:

- `AccountLabelRequest` Pydantic model
- `GET /categories` route handler
- `GET /tags` route handler
- `GET /accounts` route handler
- `PUT /accounts/{account_id}` route handler

**Verify `extra="forbid"` on `AccountLabelRequest`:**

Same check as Task 6: add `model_config = ConfigDict(extra="forbid")` if
missing.

`GET /accounts`, `GET /categories`, and `GET /tags` accept no query
parameters — do **not** add `_strict_params` (parameterless endpoints are
already covered by FastAPI's built-in handling, and health-check tools often
append cache-buster params).

**Update `server.py`:**

Remove the moved code and add:

```python
from claw_plaid_ledger.routers import accounts as accounts_router_module
...
app.include_router(accounts_router_module.router)
```

After this task `server.py` should look roughly like:

```python
"""FastAPI application instance for claw-plaid-ledger."""

from __future__ import annotations

from claw_plaid_ledger.middleware.correlation import CorrelationIdMiddleware
from claw_plaid_ledger.middleware.ip_allowlist import WebhookIPAllowlistMiddleware
from claw_plaid_ledger.routers import accounts as accounts_router_module
from claw_plaid_ledger.routers import health as health_router_module
from claw_plaid_ledger.routers import spend as spend_router_module
from claw_plaid_ledger.routers import transactions as transactions_router_module
from claw_plaid_ledger.routers import webhooks as webhooks_router_module
from claw_plaid_ledger.routers.webhooks import lifespan

import fastapi

app = fastapi.FastAPI(title="claw-plaid-ledger", lifespan=lifespan)
app.add_middleware(WebhookIPAllowlistMiddleware)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(health_router_module.router)
app.include_router(transactions_router_module.router)
app.include_router(spend_router_module.router)
app.include_router(accounts_router_module.router)
app.include_router(webhooks_router_module.router)
```

If `server.py` still defines anything beyond app construction and router
wiring, that is a defect — move it to the correct module.

**Update BUGS.md:**

Mark BUG-014 as Resolved:

```
**Status:** Resolved (Sprint 21, M19)
```

### Done when

- `routers/accounts.py` exists.
- `AccountLabelRequest` has `extra="forbid"`.
- `server.py` is ≤50 lines and contains only the app factory.
- `server.py` defines no route handlers, no Pydantic models, no middleware
  classes, and no sync logic.
- BUG-014 marked Resolved in BUGS.md.
- Quality gate passes with the same test count as before this sprint plus the
  new BUG-014 tests from Tasks 4–6.

---

## Acceptance criteria for Sprint 21

- Final module structure matches the **Target module structure** above.
- `server.py` is ≤50 lines; no route handlers, models, or helpers remain.
- All existing tests pass with the same count as before the sprint (import
  paths updated as noted per task; test logic unchanged).
- New BUG-014 tests pass: `GET /errors`, `GET /spend`, `GET /spend/trends`,
  and `GET /transactions` each return HTTP 422 for unrecognised query
  parameters with `"unrecognized"` and `"valid_parameters"` in the response
  body.
- `AnnotationRequest` and `AccountLabelRequest` both have
  `model_config = ConfigDict(extra="forbid")`.
- All quality-gate commands pass.

## Explicitly deferred

- `_strict_params` on any future router added in M20+ (apply the pattern when
  adding new parameterised GET endpoints).
- Splitting `test_db.py` (below threshold; revisit if `db.py` grows in M20+).
