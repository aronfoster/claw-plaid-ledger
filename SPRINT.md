# Sprint 20 — M18: Split Test Files

## Sprint goal

Break the test suite into focused modules so no file exceeds ~2,000 lines,
LLM context windows can cover a full test file comfortably, and shared helpers
live in one place.

## Why this sprint exists

`test_server.py` has grown to 5,249 lines across 15+ test classes covering
every API endpoint, all middleware, background sync, and scheduling logic.
Loading it in a single LLM context window is impractical, and finding tests
for a specific feature requires searching the entire file. `test_cli.py` is at
1,725 lines and will cross the threshold when M20+ adds allocation commands.

## Working agreements

- Tasks 1 and 2 are independent and may be done concurrently.
- Task 3 depends on Task 2 (both touch `test_server.py`; must be sequential).
- Each task must leave the quality gate green before the next starts.
- All Python changes must pass the full quality gate before commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- No test logic may be added, removed, or changed — this is reorganisation only.
- Mark completed tasks `✅ DONE` in this file before committing.

---

## Task 1: Expand conftest.py and split test_cli.py

### Background

`test_cli.py` is organised naturally by CLI command. Splitting along command
boundaries gives each new file a single coherent concern and keeps test
discovery straightforward.

The current `conftest.py` holds only `_isolate_env_file`. Before splitting,
audit both `test_cli.py` and `test_server.py` for helpers that will be needed
in two or more of the new files, and promote them to `conftest.py` (or a
`tests/helpers.py` module if the helpers are not fixtures). This prevents
duplication across the split files.

The most important candidate is `_seed_transactions()` from `test_server.py`
(line 434): it is used by both the future `test_server_transactions.py` and
`test_server_annotations.py`. It must end up in exactly one place before
Task 2 begins.

`_TOKEN = "test-bearer-value"` and `client = TestClient(app)` are used by
every `test_server_*.py` file. They can be defined independently in each file
(one line each with the existing `# noqa: S105` comment) or extracted to
`conftest.py` — developer's call, as long as the choice is consistent across
all server test files.

### Target file layout

```
tests/
  conftest.py          # existing _isolate_env_file + any promoted shared helpers
  test_cli_doctor.py   # ledger doctor, ledger doctor --production-preflight
  test_cli_sync.py     # ledger sync, ledger sync --item, ledger sync --all,
                       # ledger init-db, ledger serve startup
  test_cli_link.py     # ledger link
  test_cli_items.py    # ledger items, ledger overlaps, ledger apply-precedence
```

Delete `test_cli.py` once all tests are moved and the quality gate passes.

### Test routing

| Source (test_cli.py) | Target file |
|---|---|
| `test_help`, `test_doctor_*`, `test_serve_refuses_without_api_secret`, `test_serve_refuses_invalid_log_level`, `test_serve_logs_startup_info`, `test_doctor_production_preflight_*`, `test_doctor_without_preflight_*` | `test_cli_doctor.py` |
| `test_init_db_*`, `test_sync_*` | `test_cli_sync.py` |
| `test_link_*` | `test_cli_link.py` |
| `test_items_*`, `test_apply_precedence_*`, `test_overlaps_*` | `test_cli_items.py` |

### Done when

- Four new `test_cli_*.py` files exist; `test_cli.py` is deleted.
- Shared helpers used by 2+ files are in `conftest.py` (or `tests/helpers.py`),
  not duplicated.
- `pytest -v` reports the same test count as before this task.
- Quality gate passes.

---

## Task 2: Split test_server.py — endpoint tests

### Background

This task covers the HTTP API endpoint tests: the domain-facing routes that
agents and operators call directly. Move each test class (and its scoped seed
helper, if any) into a dedicated file, then delete that class from
`test_server.py`. The file shrinks with each move; Task 3 finishes the job.

Each `_seed_*` helper is used by exactly one test class (with the exception of
`_seed_transactions`, which was handled in Task 1) and travels with that class
into the new file.

### Target files

```
tests/
  test_server_health.py           # GET /health; no-auth contract
  test_server_transactions.py     # GET /transactions, GET /transactions/{id}
  test_server_annotations.py      # PUT /annotations/{transaction_id}
  test_server_spend.py            # GET /spend, GET /spend/trends
  test_server_accounts.py         # GET /accounts, PUT /accounts/{id},
                                  # GET /categories, GET /tags
  test_server_errors.py           # GET /errors
```

### Test routing

| Class / functions | Target file |
|---|---|
| `test_health_returns_200`, `test_health_returns_ok_payload`, `test_health_no_auth_required` | `test_server_health.py` |
| `TestListTransactionsEndpoint`, `TestListTransactionsRangeParam`, `TestListTransactionsAnnotations`, `TestGetTransactionDetailEndpoint`, `TestListTransactionsTagsAndSearchNotes` | `test_server_transactions.py` |
| `TestPutAnnotationEndpoint` | `test_server_annotations.py` |
| `TestGetSpendEndpoint`, `TestGetSpendRangeParam`, spend filter test class(es), `TestGetSpendTrendsEndpoint` | `test_server_spend.py` |
| `TestCategoriesEndpoint`, `TestTagsEndpoint`, `TestAccountsEndpoints` | `test_server_accounts.py` |
| `TestGetErrorsEndpoint` | `test_server_errors.py` |

### Done when

- Six new files exist.
- All listed classes and their seed helpers no longer appear in
  `test_server.py`.
- `pytest -v` reports the same test count as before this task.
- Quality gate passes.

---

## Task 3: Split test_server.py — infrastructure tests, then delete it

### Background

This task moves the remaining test classes — middleware (three files per the
M18 design decision), webhooks, background sync, scheduled sync, and
structured logging. When all classes are moved, `test_server.py` is empty and
is deleted.

`TestStructuredLogging` tests that webhook and sync errors produce
correctly-structured log records; it belongs in `test_server_correlation.py`
alongside the other correlation-ID and logging infrastructure tests.

### Target files

```
tests/
  test_server_webhooks.py      # POST /webhooks/plaid, item routing,
                               # background sync notification wiring,
                               # injected credentials
  test_server_scheduling.py    # lifespan, check_and_sync_overdue_items,
                               # scheduled_sync_loop
  test_server_auth.py          # require_bearer_token unit tests,
                               # TestProtectedRoute
  test_server_correlation.py   # CorrelationIdMiddleware, SyncRunId,
                               # TestStructuredLogging
  test_server_ip_allowlist.py  # _resolve_client_ip, _ip_in_allowlist,
                               # WebhookIPAllowlistMiddleware
```

### Test routing

| Class | Target file |
|---|---|
| `TestWebhookPlaid`, `TestWebhookItemRouting`, `TestBackgroundSyncNotificationWiring`, `TestBackgroundSyncInjectedCredentials` | `test_server_webhooks.py` |
| `TestLifespan`, `TestCheckAndSyncOverdueItems`, `TestScheduledSyncLoop` | `test_server_scheduling.py` |
| `TestRequireBearerToken`, `TestProtectedRoute` | `test_server_auth.py` |
| `TestCorrelationIdMiddleware`, `TestSyncRunId`, `TestStructuredLogging` | `test_server_correlation.py` |
| `TestResolveClientIp`, `TestIpInAllowlist`, `TestWebhookIPAllowlistMiddleware` | `test_server_ip_allowlist.py` |

### Done when

- Five new files exist.
- `test_server.py` is deleted (all content moved).
- `pytest -v` reports the same test count as before this sprint.
- No test appears in two files; no test has been dropped.
- Quality gate passes.

---

## Acceptance criteria for Sprint 20

- No test file exceeds 2,000 lines.
- `pytest -v` passes with the same test count before and after the sprint.
- `test_server.py` and `test_cli.py` are deleted; all tests migrated.
- Helpers used by two or more files live in `conftest.py` or `tests/helpers.py`,
  not duplicated.
- All quality-gate commands pass.

## Explicitly deferred

- Splitting `test_db.py` (1,365 lines, below threshold; revisit if `db.py`
  grows in M20+).
- Splitting production `server.py` into routers (tracked as M19).
