# Sprint 19 — M17: Errors Visible to OpenClaw

## Sprint goal

Surface ledger warnings and errors to OpenClaw agents via a new `GET /errors`
endpoint backed by a persistent `ledger_errors` table, so agents can run
pre-run health checks and proactive alerting without tailing logs directly.

Also: add concrete pagination mechanics to both skill docs, so agents have
unambiguous instructions for paginating `GET /transactions`.

## Why this sprint exists

Agents currently have no way to know whether the ledger has been logging
warnings or errors. Failures in background syncs, webhook handling, and
Plaid API calls are only visible in server logs. (M17)

Both skill docs describe pagination as "paginate to completion" without
specifying the parameters, terminal condition, or response shape — leaving
agents to guess or hard-code brittle offsets.

## Working agreements

- Tasks may ship as separate PRs or one combined PR at the developer's
  discretion. Dependencies: Task 1 → Task 2 → Task 3. Task 4 is independent.
- All Python changes must pass the quality gate before merge:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Doc-only changes (skill files) still run the gate to confirm no regressions.
- Mark completed tasks `✅ DONE` in this file before committing.

---

## Task 1: `ledger_errors` table and DB layer

### Background

Agents need a queryable history of ledger warnings and errors. Before the
endpoint or logging handler can be built, the persistence layer must exist:
schema, a write function, and a read function.

### Design decisions

**Table name:** `ledger_errors` — unambiguous, grep-friendly, consistent with
`account_labels` naming convention.

**Columns:**

```sql
CREATE TABLE IF NOT EXISTS ledger_errors (
    id           INTEGER PRIMARY KEY,
    severity     TEXT NOT NULL,     -- Python level name: 'WARNING', 'ERROR', 'CRITICAL'
    logger_name  TEXT NOT NULL,     -- e.g. 'claw_plaid_ledger.server'
    message      TEXT NOT NULL,
    correlation_id TEXT,            -- request_id or sync_run_id from ContextVar; NULL outside any context
    created_at   TEXT NOT NULL      -- ISO 8601 UTC, e.g. '2026-03-22T10:00:00.000000+00:00'
);
```

Add this block to `src/claw_plaid_ledger/schema.sql`. Because `schema.sql` is
executed via `executescript` (which is idempotent for `CREATE TABLE IF NOT
EXISTS`), no separate `ALTER TABLE` migration is required — `initialize_database()`
picks it up automatically on the next run.

**Retention policy:** rows older than 30 days are pruned on every insert, inside
the same transaction as the insert. Retention is not configurable in M17.

**`insert_ledger_error()` signature:**

```python
def insert_ledger_error(
    connection: sqlite3.Connection,
    severity: str,
    logger_name: str,
    message: str,
    correlation_id: str | None,
    created_at: datetime,
) -> None:
    """Insert one error row and prune rows older than 30 days."""
```

`created_at` is passed in (not derived inside the function) so callers can
control it and tests can inject deterministic timestamps.

Pruning SQL (run after the INSERT, same connection, no explicit COMMIT needed
— the caller manages the transaction or passes an autocommit connection):

```python
cutoff = (created_at - timedelta(days=30)).isoformat()
connection.execute(
    "DELETE FROM ledger_errors WHERE created_at < ?", (cutoff,)
)
```

**`LedgerErrorQuery` dataclass** (after `SpendTrendsQuery` in `db.py`):

```python
@dataclass(frozen=True)
class LedgerErrorQuery:
    """Filters for GET /errors."""

    hours: int = 24
    min_severity: str | None = None  # None / 'WARNING' → all; 'ERROR' → ERROR+
    limit: int = 100
    offset: int = 0
```

**`query_ledger_errors()` signature:**

```python
def query_ledger_errors(
    connection: sqlite3.Connection,
    query: LedgerErrorQuery,
) -> tuple[list[dict[str, object]], int]:
    """Return (rows, total) for the given filter window, newest first."""
```

- `since` is derived inside the function as
  `datetime.now(UTC) - timedelta(hours=query.hours)`.
- `min_severity` mapping (in the WHERE clause):
  - `None` or `'WARNING'` → no extra filter (table only contains WARNING+)
  - `'ERROR'` → `AND severity IN ('ERROR', 'CRITICAL')`
- Rows are ordered `created_at DESC, id DESC` (newest first).
- `total` is the count matching the same WHERE clause (ignoring limit/offset),
  identical pattern to `query_transactions()`.
- Each row dict keys: `id`, `severity`, `logger_name`, `message`,
  `correlation_id`, `created_at`.

### Scope

- `src/claw_plaid_ledger/schema.sql` — add `ledger_errors` table
- `src/claw_plaid_ledger/db.py` — add `LedgerErrorQuery`, `insert_ledger_error()`,
  `query_ledger_errors()`
- `tests/test_db.py` — new test class `TestLedgerErrors`

### Tests (`tests/test_db.py`)

Add class `TestLedgerErrors`. Use `tmp_path` + `initialize_database()` to get
a fresh DB for each test.

Minimum required tests:

- `test_insert_and_query_basic` — insert one WARNING row, query returns it
- `test_query_returns_newest_first` — insert rows with different `created_at`
  values; verify order is newest first
- `test_min_severity_error_filters_warnings` — insert one WARNING and one ERROR
  row; `min_severity='ERROR'` returns only the ERROR row
- `test_hours_window_excludes_old_rows` — insert a row 48 hours ago; `hours=24`
  query returns empty
- `test_retention_prunes_old_rows` — insert a row, then insert another row with
  `created_at` set to 31 days in the future; the first (old) row is pruned
- `test_total_reflects_full_count` — insert 5 rows matching the filter; query
  with `limit=2` returns `total=5` and `len(rows)==2`
- `test_correlation_id_nullable` — insert with `correlation_id=None`; row
  survives round-trip with `correlation_id` as `None`
- `test_empty_table_returns_zero` — fresh DB; `query_ledger_errors` returns
  `([], 0)`

### Done when

- `ledger_errors` table appears in a freshly initialized DB.
- `insert_ledger_error()` inserts a row and prunes rows older than 30 days in
  the same transaction.
- `query_ledger_errors()` returns rows ordered newest first, respects `hours`
  and `min_severity`, and returns accurate `total` independent of `limit`.
- Quality gate passes.

---

## Task 2: `LedgerDbHandler` + `GET /errors` endpoint + `doctor` integration

### Background

With the DB layer in place, this task wires everything together: a logging
handler that writes to `ledger_errors` automatically, the API endpoint that
exposes it, and `doctor` output that surfaces the table's health.

### Design decisions

**`LedgerDbHandler` placement:** add to `src/claw_plaid_ledger/logging_utils.py`.
It belongs there alongside `CorrelationIdFilter` and follows the same
"install once, benefits all loggers" pattern.

**Handler level:** `logging.WARNING`. The handler only persists WARNING, ERROR,
and CRITICAL records. DEBUG and INFO are not written to the table.

**Re-entrancy guard:** the handler opens a new `sqlite3.connect()` per `emit()`
call (same pattern the server uses per request — cheap and thread-safe for
SQLite). To prevent infinite recursion if the DB layer itself ever logs at
WARNING+, use a `threading.local()` guard:

```python
import threading

class LedgerDbHandler(logging.Handler):
    def __init__(self, db_path: Path) -> None:
        super().__init__(level=logging.WARNING)
        self._db_path = db_path
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._local, "active", False):
            return
        self._local.active = True
        try:
            with sqlite3.connect(self._db_path) as conn:
                insert_ledger_error(
                    conn,
                    severity=record.levelname,
                    logger_name=record.name,
                    message=self.format(record),
                    correlation_id=get_correlation_id() or None,
                    created_at=datetime.fromtimestamp(record.created, tz=UTC),
                )
        except Exception:  # noqa: BLE001
            self.handleError(record)
        finally:
            self._local.active = False
```

`get_correlation_id()` returns `"-"` when no context is active; store `None`
instead (use `get_correlation_id() or None`, since `"-"` is falsy-enough with
an `or None` guard — actually `"-"` is truthy, so check explicitly:
`v if (v := get_correlation_id()) != "-" else None`).

**Installation:** in `server.py`'s `lifespan()` function. When `cfg` loads
successfully, create a `LedgerDbHandler(cfg.db_path)` and add it to the root
logger. Remove it in the shutdown phase (after `yield`). This ensures all
code running inside the server process — including background sync tasks,
webhook handlers, and request handlers — has errors persisted automatically.

The CLI sync path (`ledger sync`, `ledger sync --all`) is intentionally out
of scope for M17: these commands are typically invoked interactively and have
terminal output. Extending the handler to CLI commands can be added later.

**`GET /errors` endpoint:**

Path: `GET /errors`. Requires bearer auth. Query parameters:

| param | type | default | notes |
|-------|------|---------|-------|
| `hours` | `int` | `24` | lookback window; minimum `1`; enforced with `Field(ge=1)` |
| `min_severity` | `Literal["WARNING", "ERROR"] \| None` | `None` | None = all WARNING+ |
| `limit` | `int` | `100` | max `500`; use `Query(le=500)` |
| `offset` | `int` | `0` | |

Response shape:

```json
{
  "errors": [
    {
      "id": 1,
      "severity": "ERROR",
      "logger_name": "claw_plaid_ledger.server",
      "message": "background sync failed: connection refused",
      "correlation_id": "req-a1b2c3d4",
      "created_at": "2026-03-22T10:05:00.000000+00:00"
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0,
  "since": "2026-03-21T10:05:00.000000+00:00"
}
```

`since` is the UTC datetime marking the start of the `hours` window, so
callers know the exact window without having to compute it themselves.

Endpoint implementation sketch:

```python
class ErrorListQuery(BaseModel):
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
```

**`doctor` integration:**

1. Add `"ledger_errors"` to `_EXPECTED_TABLES` in `cli.py`. The existing
   schema check then automatically reports FAIL if the table is missing.

2. After the existing `doctor: transactions rows=N` line, add a new output
   line reporting recent error counts. Add a helper to `cli.py`:

   ```python
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
       return warn, error
   ```

   Output in `doctor()`:
   ```
   doctor: error-log warn=2 error=0 (last 24h)
   ```

   This is informational — it does not cause `doctor` to exit non-zero. Agents
   and operators can see the counts and decide whether to investigate.

### Scope

- `src/claw_plaid_ledger/logging_utils.py` — add `LedgerDbHandler`
- `src/claw_plaid_ledger/server.py` — install/remove handler in `lifespan()`;
  add `ErrorListQuery` model; add `GET /errors` endpoint; import `LedgerErrorQuery`
  and `query_ledger_errors` from `db`
- `src/claw_plaid_ledger/cli.py` — add `"ledger_errors"` to `_EXPECTED_TABLES`;
  add `_doctor_error_log_stats()`; call it in `doctor()`
- `tests/test_logging_utils.py` — new tests for `LedgerDbHandler`
- `tests/test_server.py` — new test class `TestGetErrorsEndpoint`

### Tests

#### `tests/test_logging_utils.py`

Add class `TestLedgerDbHandler`. Use `tmp_path` + `initialize_database()`.

- `test_emit_warning_writes_row` — emit a WARNING record; verify row appears
  in `ledger_errors` via `query_ledger_errors()`
- `test_emit_error_writes_row` — same for ERROR
- `test_emit_info_is_ignored` — emit an INFO record; table stays empty
  (handler level is WARNING, so INFO is filtered before `emit()` is called)
- `test_emit_carries_correlation_id` — set a correlation ID via
  `set_correlation_id()`; emit WARNING; verify `correlation_id` in stored row
- `test_reentrancy_guard_prevents_recursion` — call `handler.emit()` from
  inside a mocked `insert_ledger_error` that re-emits on the same handler;
  no infinite recursion and no exception raised

#### `tests/test_server.py` — `TestGetErrorsEndpoint`

Seed the `ledger_errors` table directly using `insert_ledger_error()` (bypass
the handler) so tests are independent of logging configuration. Use the
standard `tmp_path` + `initialize_database()` + `patch("load_config", ...)`
pattern already established in the file.

Minimum required tests:

- `test_returns_200_with_auth` — authenticated request returns HTTP 200
- `test_requires_auth` — unauthenticated request returns HTTP 401
- `test_empty_table_returns_empty_list` — no rows in DB; response has
  `errors=[]`, `total=0`
- `test_returns_errors_within_hours_window` — seed one row within 24h and one
  row 48h ago; default `?hours=24` returns only the recent row
- `test_min_severity_error_excludes_warnings` — seed one WARNING and one ERROR;
  `?min_severity=ERROR` returns only the ERROR row
- `test_pagination_limit_and_offset` — seed 5 rows; `?limit=2&offset=2`
  returns 2 rows with `total=5`
- `test_since_field_present_in_response` — `since` key is present and is a
  valid ISO datetime string
- `test_hours_validation_rejects_zero` — `?hours=0` returns HTTP 422

### Done when

- `LedgerDbHandler` installed in server `lifespan()`; any WARNING+ log from
  any logger during server operation is persisted to `ledger_errors`.
- `GET /errors` appears in the auto-generated OpenAPI spec at `/openapi.json`.
- `GET /errors?hours=24` returns the correct shape with `errors`, `total`,
  `limit`, `offset`, and `since` fields.
- `?min_severity=ERROR` excludes WARNING rows.
- `?hours=0` returns HTTP 422.
- `doctor` output includes `doctor: error-log warn=N error=N (last 24h)`.
- `doctor` schema check fails if `ledger_errors` table is missing.
- Quality gate passes.

---

## Task 3: Skill doc updates + pagination mechanics

### Background

Two independent additions to the skill bundles:

1. **`GET /errors` documentation** — both agents need to know the endpoint
   exists, when to call it, and what to do with the results.

2. **Pagination mechanics** — both skill docs mention "paginate to completion"
   without explaining the parameters, response shape, or terminal condition.
   Agents have had to guess. This task adds a concrete, reusable reference.

These are doc-only changes. Run the full quality gate anyway to confirm no
regressions.

### Scope

#### Pagination mechanics (both agents)

Add the following section to **both** `skills/hestia-ledger/SKILL.md` and
`skills/athena-ledger/SKILL.md`, as a new `## Pagination` top-level section
(place it after `## Approved API calls` and before the first workflow/playbook
section):

```markdown
## Pagination

`GET /transactions` supports offset-based pagination via `limit` and `offset`
query parameters. Every list response includes:

```json
{
  "transactions": [...],
  "total": 247,
  "limit": 100,
  "offset": 0
}
```

- `limit` — number of rows per page; default `100`, maximum `500`.
- `offset` — zero-based row index of the first row on this page.
- `total` — total number of rows matching the query (independent of limit/offset).

**To paginate to completion:**

1. Start with `offset=0` and a fixed `limit` (e.g. `100`). Keep `limit` stable
   within a run.
2. After each response, advance: `offset += limit`.
3. Stop when `offset >= total` — the next page would be empty.

Equivalently: stop when the number of rows returned is less than `limit`
(the server returned a partial page, meaning this was the last).

**If pagination is interrupted** (call fails or run is aborted mid-way),
report partial coverage and avoid definitive completeness claims.
```

Also add matching pagination mechanics to both agents' `query_playbooks.md`
files. Add a `## Pagination reference` section at the top of the Global rules
block, or as its own section directly below it:

```markdown
## Pagination mechanics

Response shape for `GET /transactions`:
`{ "transactions": [...], "total": N, "limit": L, "offset": O }`

- Advance: `offset += limit` after each page.
- Stop: when `offset >= total`.
- Keep `limit` stable within a run (recommended: `100`).
- If interrupted: report partial coverage; do not make totals claims.
```

#### `GET /errors` — Hestia

**`skills/hestia-ledger/SKILL.md`**

1. Add to **Approved API calls**:

   ```
   8. `GET /errors` — recent ledger warnings and errors; use as a pre-run
      health check before each ingestion run
   ```

2. Add a **Pre-run health check** step at the top of the
   **Deterministic ingestion loop** section (renumber existing steps):

   ```markdown
   0. **Pre-run health check.** Call `GET /errors?hours=1&min_severity=ERROR`
      before starting ingestion. If the response contains any ERROR-level rows,
      surface them in the run frame output and lower overall confidence for
      the run. Do not abort — continue ingestion with reduced confidence and
      flag any affected results.
   ```

**`skills/hestia-ledger/checklists/query_playbooks.md`**

Add to the **Vocabulary setup (start of run)** block:

```markdown
4. `GET /errors?hours=1&min_severity=ERROR` — check for recent server errors
   before beginning ingestion. Surface any ERROR rows in the run frame; lower
   confidence if present.
```

#### `GET /errors` — Athena

**`skills/athena-ledger/SKILL.md`**

1. Add to **Approved API calls**:

   ```
   10. `GET /errors` — recent ledger warnings and errors; use for proactive
       alerting and pre-analysis health checks
   ```

2. Add a new workflow section after **5) Anomaly narrative workflow**:

   ```markdown
   ### 6) Proactive error alerting

   Call `GET /errors` when preparing a periodic summary or when a user asks
   about ledger health. Useful parameters:

   - `?hours=24` — last day (default)
   - `?hours=168` — last week
   - `?min_severity=ERROR` — errors only (exclude warnings)

   If ERROR-level rows are present, include them in the summary and recommend
   the operator investigate. Warnings may be informational — note them but
   do not escalate unless they form a pattern.
   ```

**`skills/athena-ledger/checklists/query_playbooks.md`**

Add a new playbook entry after **7) Month-over-month trends**:

```markdown
### 8) Ledger health check

1. `GET /errors?hours=24` — retrieve warnings and errors from the last 24h.
2. If `total > 0`, group rows by `severity` and `logger_name`.
3. For ERROR-level rows: include in any summary report with the `message` and
   `correlation_id` for operator follow-up.
4. For WARNING-level rows: note the count; escalate only if they form a
   repeating pattern or accompany anomalous transaction data.
5. To broaden the window: `?hours=168` (last 7 days).
6. To narrow to errors only: add `?min_severity=ERROR`.
```

### Done when

- Both skill `SKILL.md` files contain a `## Pagination` section with concrete
  `limit`/`offset`/`total` mechanics and a clear terminal condition.
- Both `query_playbooks.md` files contain a pagination reference.
- Hestia's approved API calls list includes `GET /errors`; her ingestion loop
  documents a step-0 pre-run health check.
- Athena's approved API calls list includes `GET /errors`; her skill doc
  documents workflow 6 (proactive alerting); her playbook includes entry 8.
- Quality gate passes (no Python regressions from doc-only change).

---

## Acceptance criteria for Sprint 19

- `ledger_errors` table exists after `ledger init-db` on a fresh installation.
- Any WARNING or ERROR logged by any logger during server operation is
  automatically persisted to `ledger_errors` without any per-call
  instrumentation.
- `GET /errors` returns `{ errors, total, limit, offset, since }`. Rows are
  ordered newest first.
- `?hours=N` limits results to the last N hours (minimum 1; `?hours=0` → 422).
- `?min_severity=ERROR` excludes WARNING rows.
- `?limit`/`?offset` paginate the result set; `total` is always the full count.
- Endpoint requires bearer-token auth; unauthenticated requests return 401.
- `ledger doctor` reports `doctor: error-log warn=N error=N (last 24h)`.
- `ledger doctor` schema check FAILs if `ledger_errors` table is absent.
- Both skill docs contain concrete pagination mechanics (limit/offset/total).
- Both agents have `GET /errors` in their approved call lists with usage guidance.
- All quality-gate commands pass.

## Explicitly deferred (out of scope for Sprint 19)

- Installing `LedgerDbHandler` on CLI sync commands (`ledger sync`,
  `ledger sync --all`). The handler covers all server-context code paths.
- Configurable retention period (hard-coded 30 days is sufficient for M17).
- `GET /errors` on Hestia's approved list as a spend/analysis endpoint
  (Hestia's use is limited to the pre-run health check pattern documented here).
- Splitting `tests/test_server.py` into focused modules (deferred to M18).
