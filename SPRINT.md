# Sprint 12 — M11: Advanced Agent API & Logging

## Sprint goal

Equip Hestia with richer query capabilities and make the system observable
from logs alone. By the end of this sprint the agent API surfaces a total-spend
endpoint, allows tag-based and annotation-notes filtering, and every request and
sync run carries a correlation ID that appears consistently across all log lines.
Debug logs are audited to ensure no secrets are emitted.

## Working agreements

- Keep each task reviewable in one PR where possible.
- All existing endpoints, CLI commands, and workflows must remain backward-
  compatible. New query parameters must be optional with sensible defaults.
- Raw ingestion and sync engine behavior are untouched in this sprint.
- Run the quality gate before every commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Add or update tests for every behavior change.
- No new runtime dependencies without explicit justification.

---

## Task 1: Total spend summary endpoint ✅ DONE

### Scope

Add `GET /spend` — a purpose-built analytics endpoint that returns aggregate
spend totals for a configurable date window and optional tag filter. This lets
Hestia answer questions like "how much did Alice spend on groceries last month?"
with a single API call instead of paginating through all transactions and
aggregating client-side.

### New endpoint

```
GET /spend
```

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `start_date` | str (ISO date) | required | Window start, inclusive. |
| `end_date` | str (ISO date) | required | Window end, inclusive. |
| `owner` | str | `None` | Restrict to accounts belonging to this owner tag. |
| `tags` | list[str] | `[]` | Restrict to transactions whose annotation contains **all** of the listed tags (AND semantics). Pass the parameter multiple times for multiple tags: `?tags=groceries&tags=recurring`. |
| `include_pending` | bool | `false` | When `false` (default), exclude pending transactions. When `true`, include them. Pending amounts are unconfirmed so the default is conservative. |
| `view` | `"canonical"` \| `"raw"` | `"canonical"` | Same semantics as `GET /transactions`. |

**Response body:**

```json
{
  "start_date": "2025-01-01",
  "end_date": "2025-01-31",
  "total_spend": 1234.56,
  "transaction_count": 42,
  "includes_pending": false,
  "filters": {
    "owner": "alice",
    "tags": ["groceries"]
  }
}
```

- `total_spend` is the **sum of `amount` values** for matching transactions.
  Plaid uses positive amounts for debits (money leaving the account) and
  negative for credits. Sum as-is; do not negate.
- `transaction_count` is the count of rows matched (before any aggregation).
- Both fields are `0` / `0` when no transactions match — never `null`.
- `start_date` and `end_date` are echoed back in the response for
  unambiguous client-side logging.
- Require `start_date` and `end_date`; return HTTP 422 if either is absent or
  unparseable as an ISO date.

### Implementation notes

1. **Database query** — join `transactions` with `annotations` (LEFT JOIN) to
   access tags. Tags are stored as a JSON string; use SQLite's `json_each()` to
   filter by tag values. Filter by `posted_date` (non-pending) or
   `authorized_date` (pending) as appropriate when `include_pending=false`.
   The simplest correct approach: when `include_pending=false`, add
   `AND pending = 0`; when `true`, no pending filter.
2. **Tag filtering** — for each tag in the `tags` list, add a subquery or
   EXISTS clause that checks `json_each(annotations.tags)`. All tags must match
   (AND). An unannotated transaction never matches a tag filter.
3. **`owner` filter** — join to `accounts` on `plaid_account_id` and filter
   by `accounts.owner`. Reuse the same logic as the `view=canonical` filter
   in `GET /transactions`.
4. **Auth** — Bearer token required (same as all other endpoints).
5. **OpenAPI** — the endpoint must appear in `/docs` with clear parameter and
   response descriptions.

### Done when

- `GET /spend?start_date=X&end_date=Y` returns `total_spend` and
  `transaction_count` for all canonical non-pending transactions in the window.
- `?tags=groceries` restricts to transactions annotated with that tag.
- `?tags=groceries&tags=recurring` restricts to transactions with both tags.
- `?include_pending=true` includes pending transactions in the total.
- `?owner=alice` restricts to Alice's accounts.
- Missing `start_date` or `end_date` returns HTTP 422.
- An empty date window (no matching transactions) returns
  `{"total_spend": 0, "transaction_count": 0, ...}` not an error.
- Tests cover: basic spend in window; tag filter (single tag); tag filter (AND
  — two tags); `include_pending` true vs false; `owner` filter;
  `view=raw`; missing required params → 422; empty result window → zeros;
  unauthenticated request → 401.
- All quality gates pass.

---

## Task 2: Enhanced transaction filtering — tags and annotation notes ✅ DONE

### Scope

Extend `GET /transactions` with two new optional filter parameters:
tag-based filtering and annotation-note search. The existing `keyword`
parameter searches `name` and `merchant_name`; extend it to also match the
annotation `note` field. Add a separate `tags` parameter (same semantics as
the spend endpoint).

These are purely additive changes — all existing parameters and their behavior
are unchanged.

### New parameters on `GET /transactions`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tags` | list[str] | `[]` | Return only transactions whose annotation contains **all** listed tags (AND semantics). Same multi-value query string convention as `/spend`. Transactions without an annotation never match a tag filter. |
| `search_notes` | bool | `false` | When `true`, include the annotation `note` field in `keyword` searches in addition to `name` and `merchant_name`. Has no effect if `keyword` is absent. |

### Implementation notes

1. **`tags` filter** — add a LEFT JOIN to `annotations` in the existing
   transactions list query (or extend the join if one already exists). Apply
   the same `json_each(annotations.tags)` existence check used in Task 1.
   Reuse the helper logic from Task 1 to avoid duplication.
2. **`search_notes`** — when `search_notes=true` and `keyword` is set, add an
   additional OR clause: `OR annotations.note LIKE '%<keyword>%'`. The join to
   annotations must remain a LEFT JOIN so transactions without annotations still
   appear when `tags` is empty and `search_notes` is false.
3. **`total` count** — the `total` field in the response must reflect the
   count of matching rows after the new filters are applied, for correct
   pagination.
4. **No breaking changes** — existing callers sending none of the new params
   must receive identical results to today. Add regression tests that confirm
   this.

### Done when

- `GET /transactions?tags=groceries` returns only transactions annotated with
  `"groceries"`.
- `GET /transactions?tags=a&tags=b` returns only transactions annotated with
  both tags.
- `GET /transactions?keyword=coffee` still searches only `name` and
  `merchant_name` (unchanged baseline).
- `GET /transactions?keyword=coffee&search_notes=true` also matches
  transactions whose annotation `note` contains "coffee".
- `GET /transactions` with no new params returns results identical to pre-sprint
  behavior (regression test).
- `total` in the response correctly reflects the filtered count for pagination.
- Tests cover: tag filter (match, no-match, AND-two-tags); `search_notes=true`
  matches note; `search_notes=false` does not match note; combined `tags` +
  `keyword` + `search_notes`; no-new-params regression.
- All quality gates pass.

---

## Task 3: Structured logging with correlation IDs ✅ DONE

### Scope

Introduce consistent structured logging across the API, CLI, and sync layers.
Every HTTP request gets a unique `request_id`; every sync run gets a unique
`sync_run_id`. Both IDs propagate through all log lines emitted during their
lifetime. Audit all existing log sites to ensure DEBUG logs never emit secrets.
Where possible, include redacted webhook payloads at DEBUG level.

### Logging conventions

**Format change** — update `logging.basicConfig` format string to include
correlation fields. Proposed format:

```
%(asctime)s %(levelname)s %(name)s [%(correlation_id)s]: %(message)s
```

Use Python's `logging.LoggerAdapter` or a custom `logging.Filter` to inject
`correlation_id` into every log record from within a request or sync context.
When no context is active, `correlation_id` should render as `-`.

**ID generation** — use `uuid.uuid4()` short hex (first 8 characters is
sufficient for home-server scale): e.g. `req-a1b2c3d4`, `sync-e5f6a7b8`.

### Request correlation IDs (API layer)

1. Add a FastAPI middleware (not a dependency) that:
   - Generates a `request_id = "req-" + uuid4().hex[:8]` per request.
   - Stores it in a `contextvars.ContextVar` so all code in the request
     call stack can read it without passing it explicitly.
   - Emits an INFO log at request start:
     `"request_start method={METHOD} path={PATH} request_id={ID}"`
   - Emits an INFO log at request end:
     `"request_end method={METHOD} path={PATH} status={STATUS} request_id={ID}"`
   - Adds `X-Request-Id: {ID}` to the response headers.
2. All existing log calls inside request handlers automatically pick up the
   `request_id` via the context var — no manual threading needed.
3. When a webhook triggers `_background_sync()`, pass the `request_id` as the
   `sync_run_id` seed or prefix (e.g. `"sync-{request_id_suffix}"`). This
   links the webhook log entry to the subsequent sync log entries.

### Sync correlation IDs (sync layer)

1. `_background_sync()` and `run_sync()` generate (or accept) a
   `sync_run_id = "sync-" + uuid4().hex[:8]`.
2. All log lines emitted inside a sync run — including those in
   `sync_engine.py` — include `sync_run_id` in the message or via the log
   adapter. Passing the ID explicitly as a parameter to `run_sync()` is
   acceptable and preferred over a global context var for the sync layer, to
   keep the sync engine testable without FastAPI machinery.
3. The scheduled sync loop logs its own `sync_run_id` per item per check pass.

### Secret audit and redaction rules

Review every `logger.debug(...)` call in the codebase. Apply these rules:

| Data type | Rule |
|---|---|
| Bearer tokens (`CLAW_API_SECRET`, `Authorization` header value) | **Never log**, not even at DEBUG. |
| Plaid secrets (`PLAID_SECRET`, `PLAID_WEBHOOK_SECRET`) | **Never log**, not even at DEBUG. |
| Plaid access tokens | **Never log**. |
| Webhook request body | **OK to log at DEBUG** after redacting the `Authorization` header. Strip any field named `secret`, `token`, or `password` from the logged dict. Financial data and account IDs are fine. |
| Transaction data, account IDs, amounts | OK at DEBUG and INFO. |
| Sync cursors | OK at DEBUG. |

Add a small `redact_webhook_body(body: dict) -> dict` helper (in a suitable
module) that removes the above sensitive keys before logging. Cover it with
a unit test.

### Implementation notes

- `contextvars.ContextVar` is the right primitive for the request-scoped ID
  in an async FastAPI context; it is safe under `asyncio` concurrency.
- Do not introduce a new library dependency (e.g. `structlog`). Standard
  `logging` with a `LoggerAdapter` or `Filter` is sufficient.
- The CLI `sync` command (not the server) should emit a `sync_run_id` in its
  log output so that manual syncs are also traceable.
- The `lifespan` startup/shutdown log lines should emit a context-free marker
  (e.g. `[startup]` or `correlation_id=-`).

### Logging coverage audit

As a housekeeping pass, read through all modules (`server.py`, `sync_engine.py`,
`cli.py`, `db.py`, `config.py`, `preflight.py`, `notifications.py`, and any
others) and identify code paths that are currently silent but would be useful
for operators during normal use or when diagnosing problems.

**Guiding questions when reviewing a code path:**
- If this fails or behaves unexpectedly, would a log make the root cause
  immediately obvious?
- Are there non-trivial decisions or branch points that an operator might want
  to trace?
- Is there a success path that currently gives no feedback at all?

**Examples of gaps likely to be found** (verify against the actual code; do not
assume these exist):
- `db.py` database migration or schema bootstrap — does it log which migration
  was applied or if the schema was already current?
- `config.py` — does it log which `.env` file was loaded (path, not contents)?
- `preflight.py` — does each check log its own pass/fail at DEBUG so a full
  preflight trace is readable without looking at source?
- `notifications.py` — does a successful OpenClaw notification log the
  response status? Does a failed one log the error clearly enough to diagnose
  without a stack trace?
- `cli.py` `items` command — does it log anything useful when `items.toml` is
  absent vs. present?
- Annotation writes (`PUT /annotations`) — is there a DEBUG log of what was
  written and for which transaction?

For each gap found, add a log call at the appropriate level:
- `DEBUG` for high-frequency events or detailed state that is only useful when
  actively debugging.
- `INFO` for significant lifecycle events (server start, sync complete,
  migration applied, notification sent).
- `WARNING` for recoverable anomalies an operator should know about.

Do not add log calls that just echo what is already obvious from the surrounding
context, and do not add log calls inside tight loops that would flood output at
INFO level. Quality over quantity.

### Done when

- Every HTTP request log line includes `request_id`.
- `X-Request-Id` header is present in all API responses.
- Every sync run log line (including those inside `sync_engine.py`) includes
  `sync_run_id`.
- A webhook-triggered sync links its `sync_run_id` back to the triggering
  `request_id` in at least one log line.
- `ledger sync` CLI command logs a `sync_run_id`.
- No bearer token, Plaid secret, or access token appears in any log at any
  level.
- `redact_webhook_body()` exists, is unit-tested, and is used before any
  DEBUG log of a webhook payload.
- Log lines emitted outside any request/sync context render `correlation_id`
  as `-` (not a crash, not blank).
- Logging coverage audit is complete; gaps found are filled with appropriately
  levelled log calls.
- Tests cover: middleware generates unique IDs per request; response includes
  `X-Request-Id`; `redact_webhook_body` removes all sensitive keys and
  preserves financial data; sync run ID is present in mock-logged sync output.
- All quality gates pass.

---

## Task 4: Sprint closeout, docs, and acceptance validation ✅ DONE

### Scope

Update project documentation to reflect the M11 implementation and validate
all acceptance criteria.

### Checklist

- `ARCHITECTURE.md`:
  - Add `GET /spend` to the API endpoint reference.
  - Document the new `tags` and `search_notes` parameters on `GET /transactions`.
  - Add a "Logging conventions" section describing the correlation ID scheme,
    log format, and secret-redaction policy.
- `RUNBOOK.md`:
  - Add a short troubleshooting tip: "Tracing a request end-to-end using
    `request_id` and `sync_run_id`" — one or two grep examples.
- `ROADMAP.md`:
  - Move M11 from "Upcoming Milestones" to "Completed Milestones".
- `SPRINT.md`:
  - Append `✅ DONE` to each completed task heading.
  - Add "Sprint 12 closeout ✅ DONE" section summarising what shipped and
    any explicitly deferred follow-ups.
- Quality gate must pass at closeout:
  - `uv run --locked ruff format . --check` ✅
  - `uv run --locked ruff check .` ✅
  - `uv run --locked mypy .` ✅
  - `uv run --locked pytest -v` ✅

---

## Acceptance criteria for Sprint 12

- `GET /spend` returns accurate aggregate totals for date-window and tag queries.
- `GET /spend` excludes pending transactions by default; includes them with
  `?include_pending=true`.
- `GET /transactions?tags=...` filters correctly; `?search_notes=true` extends
  keyword search to annotation notes.
- All pre-existing `GET /transactions` call patterns return identical results
  to Sprint 11 (no regressions).
- Every API response includes `X-Request-Id`.
- Every log line emitted during a request includes the request correlation ID.
- Every log line emitted during a sync run includes the sync run ID.
- No secrets (bearer tokens, Plaid secrets, access tokens) appear in logs at
  any level.
- Webhook payloads can appear at DEBUG level after redaction.
- All existing workflows (`doctor`, `sync`, `serve`, `items`, `link`,
  `preflight`, `apply-precedence`, `overlaps`) are unbroken.
- Quality gate passes.

## Explicitly deferred (remain out of scope in Sprint 12)

- Parallel multi-institution sync.
- Automatic `apply-precedence` on every sync.
- Per-agent token scoping.
- Markdown export.
- M12 Hestia skill definition (next milestone).

---

## Sprint 12 closeout ✅ DONE

### What shipped

- `GET /spend` endpoint is live with required date params, canonical/raw view
  support, owner filtering, pending controls, and AND-semantics tag filters.
- `GET /transactions` now supports AND-semantics `tags` filtering and
  `search_notes=true` to extend keyword matching into annotation notes while
  preserving no-new-params behavior.
- Structured logging now carries correlation IDs across request and sync flows:
  request middleware emits `request_id`, sync paths emit `sync_run_id`, and
  API responses include `X-Request-Id`.
- Secret-safe logging guardrails are in place, including webhook payload
  redaction before DEBUG-level logging.
- Documentation has been updated across architecture, runbook, roadmap, and
  readme to reflect M11 behavior and operator workflows.

### Deferred follow-ups (explicit)

- Parallel multi-institution sync remains deferred.
- Automatic `apply-precedence` on every sync remains deferred.
- Per-agent token scoping remains deferred.
- Markdown export remains deferred.
- M12 Hestia skill definition remains deferred to the next milestone.
