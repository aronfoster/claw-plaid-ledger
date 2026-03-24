# Sprint 21 — M19: Split server.py into routers

## Sprint goal

Decompose the 1 054-line `server.py` monolith into a proper FastAPI router
structure so that each domain has its own file, the app factory is thin, and
M20 can add an `allocations` router without touching every other concern.

This sprint also resolves **BUG-014** (unknown query parameters silently
ignored) by introducing `_strict_params` in `routers/utils.py` and wiring it
into every parameterised GET endpoint as each router is created.

## Scope

- Pure structural refactor — **zero API behaviour change, zero schema change**,
  no new endpoints.
- BUG-014 (`_strict_params`) is the only new runtime behaviour.
- Quality gate must pass identically before and after **every task**.

## Target module structure

```
src/claw_plaid_ledger/
  server.py               # app factory only (~50 lines)
  middleware/
    __init__.py
    auth.py               # require_bearer_token, _bearer_scheme
    correlation.py        # CorrelationIdMiddleware
    ip_allowlist.py       # WebhookIPAllowlistMiddleware, _resolve_client_ip,
                          # _ip_in_allowlist; imports _WEBHOOK_PATH from
                          # routers.webhooks
  routers/
    __init__.py
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
- All Python changes must pass the full quality gate before commit.
- Mark completed tasks `✅ DONE` in this file before committing.

---

## Task 1: Create the middleware package ✅ DONE

Move `require_bearer_token`, `CorrelationIdMiddleware`, and
`WebhookIPAllowlistMiddleware` (plus their helpers) out of `server.py` into
dedicated modules under a new `middleware/` package.

**Symbols to move:**

| Symbol | Destination |
|---|---|
| `_bearer_scheme`, `require_bearer_token` | `middleware/auth.py` |
| `CorrelationIdMiddleware` | `middleware/correlation.py` |
| `_resolve_client_ip`, `_ip_in_allowlist`, `WebhookIPAllowlistMiddleware` | `middleware/ip_allowlist.py` |

Also move `_WEBHOOK_PATH` to `middleware/ip_allowlist.py` for now — it will
move to its canonical home in `routers/webhooks.py` in Task 3.

**Test import updates** (logic unchanged):

| Test file | Symbol | New location |
|---|---|---|
| `test_server_auth.py` | `require_bearer_token` | `middleware.auth` |
| `test_server_ip_allowlist.py` | `_resolve_client_ip`, `_ip_in_allowlist` | `middleware.ip_allowlist` |

### Done when

- `server.py` no longer defines any of the symbols listed above.
- Quality gate passes.

---

## Task 2: Create the routers scaffold — utils.py and _strict_params ✅ DONE

Create the `routers/` package and the shared utilities module. No routes move
in this task.

**`routers/utils.py`** should contain:

- `_SpendRange` — moved from `server.py`
- `_today()` — moved from `server.py`
- `_resolve_spend_dates()` — moved from `server.py`
- `_strict_params()` — new, per BUG-014 spec in BUGS.md

Update `server.py` to import `_SpendRange`, `_today`, and `_resolve_spend_dates`
from `routers.utils` so the remaining routes continue to compile.

### Done when

- `routers/utils.py` exists with all four items.
- `server.py` no longer defines those three symbols.
- Quality gate passes.

---

## Task 3: Create routers/webhooks.py ✅ DONE

Move all background-sync logic, the scheduled-sync infrastructure, the
`lifespan` context manager, and `POST /webhooks/plaid` into `routers/webhooks.py`.

**Symbols to move** (all currently in `server.py`):

`_SYNC_UPDATES_AVAILABLE`, `_WEBHOOK_PATH`, `_SCHEDULED_SYNC_POLL_INTERVAL_SECONDS`,
`_background_sync`, `_load_sync_states`, `_hours_since_sync`,
`_sync_item_if_overdue`, `_check_multi_item`, `_check_and_sync_overdue_items`,
`_scheduled_sync_loop`, `lifespan`, and the `POST /webhooks/plaid` route handler.

**`_WEBHOOK_PATH` becomes canonical here.** Update `middleware/ip_allowlist.py`
to import it from `routers.webhooks` rather than defining it locally.
There is no circular import: `ip_allowlist` → `routers.webhooks` → `middleware.auth`
forms a directed chain with no cycle.

**`server.py`** imports `lifespan` from `routers.webhooks` and passes it to
`FastAPI()`, then includes the webhooks `APIRouter`.

**Test import updates:**

| Test file | Symbols | New location |
|---|---|---|
| `test_server_sync.py` | `_background_sync`, `_check_and_sync_overdue_items`, `_scheduled_sync_loop`, `lifespan` | `routers.webhooks` |
| `test_server_logging.py` | `_background_sync` | `routers.webhooks` |

### Done when

- `server.py` contains no sync, scheduling, or webhook route code.
- `middleware/ip_allowlist.py` imports `_WEBHOOK_PATH` from `routers.webhooks`.
- Quality gate passes.

---

## Task 4: Create routers/health.py — BUG-014 on GET /errors ✅ DONE

Move `ErrorListQuery`, `GET /health`, and `GET /errors` to `routers/health.py`.

Wire `_strict_params` onto `GET /errors` using the valid parameter set from
BUGS.md. Do **not** add it to `GET /health` — monitoring tools commonly append
cache-buster params to health checks.

**Add BUG-014 tests to `test_server_errors.py`** per the test requirements in
BUGS.md (misspelled param → 422 with `"unrecognized"` and `"valid_parameters"`;
valid param → 200).

### Done when

- `GET /errors` rejects unknown query parameters with HTTP 422.
- New tests pass.
- Quality gate passes.

---

## Task 5: Create routers/spend.py — BUG-014 on GET /spend and GET /spend/trends ✅ DONE

Move `SpendListQuery`, `SpendTrendsListQuery`, `GET /spend`, and
`GET /spend/trends` to `routers/spend.py`. Import `_SpendRange`, `_today`,
and `_resolve_spend_dates` from `routers.utils`.

Wire `_strict_params` onto both endpoints using the valid parameter sets from
BUGS.md.

**Add BUG-014 tests** to `test_server_spend.py` and `test_server_spend_trends.py`
per the test requirements in BUGS.md.

### Done when

- Both spend endpoints reject unknown query parameters with HTTP 422.
- New tests pass.
- Quality gate passes.

---

## Task 6: Create routers/transactions.py — BUG-014 on GET /transactions ✅ DONE

Move `TransactionListQuery`, `_fetch_transaction_with_annotation`,
`GET /transactions`, `GET /transactions/{id}`, `AnnotationRequest`, and
`PUT /annotations/{id}` to `routers/transactions.py`. Import `_SpendRange`
and `_resolve_spend_dates` from `routers.utils`.

Wire `_strict_params` onto `GET /transactions` only — the `/{id}` and
`PUT` routes accept no query parameters.

Also verify that `AnnotationRequest` has `model_config = ConfigDict(extra="forbid")`
per BUG-014 notes in BUGS.md; add it if missing.

**Add BUG-014 tests to `test_server_transactions.py`** per the test requirements
in BUGS.md.

### Done when

- `GET /transactions` rejects unknown query parameters with HTTP 422.
- `AnnotationRequest` has `extra="forbid"`.
- New tests pass.
- Quality gate passes.

---

## Task 7: Create routers/accounts.py — finish thinning server.py

Move `AccountLabelRequest`, `GET /accounts`, `PUT /accounts/{account_id}`,
`GET /categories`, and `GET /tags` to `routers/accounts.py`.

Verify that `AccountLabelRequest` has `model_config = ConfigDict(extra="forbid")`
per BUG-014 notes in BUGS.md; add it if missing.

No `_strict_params` on these endpoints — the GET routes are parameterless.

After wiring the router in `server.py`, the file should contain only imports,
middleware registration, router inclusion, and the `FastAPI()` call — no route
handlers, Pydantic models, or helpers. If anything else remains, move it.

Mark BUG-014 as **Resolved (Sprint 21, M19)** in BUGS.md.

### Done when

- `AccountLabelRequest` has `extra="forbid"`.
- `server.py` is ≤50 lines with no route handlers or business logic.
- BUG-014 marked Resolved in BUGS.md.
- Quality gate passes.

---

## Acceptance criteria for Sprint 21

- Final module structure matches the **Target module structure** above.
- `server.py` is ≤50 lines; no route handlers, models, or helpers remain.
- All existing tests pass with the same count as before the sprint.
- BUG-014 tests pass for `GET /errors`, `GET /spend`, `GET /spend/trends`,
  and `GET /transactions`.
- `AnnotationRequest` and `AccountLabelRequest` both have `extra="forbid"`.
- All quality-gate commands pass.

## Explicitly deferred

- `_strict_params` on new endpoints added in M20+ (apply the pattern when
  adding new parameterised GET endpoints).
- Splitting `test_db.py` (below threshold; revisit if `db.py` grows in M20+).
