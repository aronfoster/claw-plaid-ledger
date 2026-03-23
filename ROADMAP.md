# Roadmap

## Completed Milestones

### M0 — Project skeleton
Python + uv baseline, strict quality tooling (ruff, mypy, pytest),
environment-backed configuration, CLI entrypoints, and SQLite bootstrap.

### M1 — Plaid connection and initial sync
Plaid client integration, cursor-based idempotent transaction sync into
SQLite, typed internal models isolated behind an adapter boundary.

### M2 — Local ledger hardening
Deterministic persistence for accounts, transactions, and sync state.
Fixed account-count inflation on multi-page syncs (BUG-001). Replaced the
hand-rolled CLI shim with real Typer (BUG-002). `doctor` command with real
diagnostics. Configurable `CLAW_PLAID_LEDGER_ITEM_ID` for multi-institution
households. Mid-loop exception handling preserves cursor integrity.

### M3 — Server skeleton and webhook receiver
FastAPI server with `ledger serve`. `GET /health`. `POST /webhooks/plaid`
with Plaid HMAC-SHA256 signature verification. Background sync triggered on
`SYNC_UPDATES_AVAILABLE`. Static bearer token auth (`CLAW_API_SECRET`)
required at startup.

### M4 — Agent API and annotation layer
Typed REST API for OpenClaw agents. `GET /transactions` with eight filter
parameters and pagination. `GET /transactions/{id}` with merged annotation.
`PUT /annotations/{transaction_id}` for durable agent-authored annotations.
`annotations` table fully isolated from the sync engine. Auto-generated
OpenAPI spec at `/openapi.json` and Swagger UI at `/docs`.

### M5 — OpenClaw notification
Webhook-triggered background sync now wakes OpenClaw/Hestia only when
transaction changes are non-zero. Notification delivery is configurable via
`OPENCLAW_HOOKS_*`, failures are non-fatal, and `doctor` reports
notification readiness.

### M6 — Multi-institution management
Household multi-item sync is implemented via `items.toml` with
`ledger sync --all` and `ledger sync --item <id>`. Per-item `owner` tags are
stored on `sync_state` and `accounts`, `doctor` reports per-item sync status,
and the legacy single-item env-var path remains compatible.

### M7 — Production operations and runbook
Committed `RUNBOOK.md` covering Plaid production prerequisites, cost model,
access-token lifecycle, sandbox/production isolation, manual migration
checklist, backup/recovery, and incident triage. `ledger doctor
--production-preflight` validates live-readiness configuration without
contacting any external service, exiting non-zero on any required failure.
Sandbox `PLAID_ENV` emits a warning without blocking. Dedicated `preflight.py`
module keeps all check logic pure and unit-testable.

### M8 — Multi-item management
`ledger link` guides the operator through the complete Plaid Link browser flow
using a self-contained local HTTP server (`127.0.0.1:18790`) and prints the
resulting `access_token` and ready-to-paste `items.toml` snippet.
`ledger items` provides an at-a-glance health view of every configured item:
token presence, account count, and last-synced timestamp. `items.toml.example`
committed at repo root with a concrete alice/bob/bank-alice/card-bob two-person
household structure. `ledger sync --all` established in docs as the standard
household ingestion path. `RUNBOOK.md` updated with daily operations and
`ledger link` walkthrough.

### M9 — Canonical household views (source precedence)
Configuration now supports per-item `suppressed_accounts` mappings in
`items.toml`. `ledger apply-precedence` writes canonical source precedence to
`accounts.canonical_account_id` and clears stale mappings. `ledger overlaps`
reports suppression status (`IN DB`, `MISMATCH`, `NOT YET SYNCED`) and flags
potential unconfirmed overlaps by shared account metadata. The API now defaults
to canonical household transactions (`GET /transactions` with `view=canonical`)
while preserving full raw access via `?view=raw`. Transaction detail responses
include `suppressed_by` provenance when a row comes from a suppressed account.

### M10 — Automation & connectivity

Webhook-first multi-item routing is implemented: `POST /webhooks/plaid`
extracts `item_id` from the payload, matches it against `items.toml`, and
passes the correct access token to `_background_sync()`.  Unknown item IDs
log a WARNING and fall back to the `PLAID_ACCESS_TOKEN` singleton.
`_background_sync()` accepts optional `access_token`, `item_id`, and `owner`
parameters while remaining fully backward-compatible with no-argument callers.
An opt-in scheduled sync fallback loop (`CLAW_SCHEDULED_SYNC_ENABLED=true`)
wakes every 60 minutes and syncs any item silent for longer than
`CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` (default 24 h; minimum 1).  `doctor`
reports the scheduled sync state.  `scripts/duckdns-update.sh` and
`RUNBOOK.md` sections 10–11 provide a complete DuckDNS setup walkthrough and
scheduled sync operations note.

### M11 — Advanced agent API & logging

Advanced agent-query and observability features are now complete. The API now
includes `GET /spend` (date-window totals with owner/tag filters and pending
controls) and enhanced `GET /transactions` filtering (`tags` + optional
annotation-note keyword search via `search_notes=true`).

Structured correlation logging is implemented across API, CLI, and sync layers:
request-scoped `request_id`, sync-scoped `sync_run_id`, `X-Request-Id` response
headers, and webhook payload redaction policies that prevent logging secrets.


### M12 — Hestia skill definition

Hestia's deterministic operating contract was delivered as a copy-ready skill
bundle. The guidance enforces ingestion-only behavior, annotation hygiene, and
explicit escalation tagging for uncertain cases.

### M12a — Two-agent redirect (Sprint 14)

Sprint 14 is complete. The single-agent contract was split into two copy-ready
skill bundles with clear runtime boundaries:

- `skills/hestia-ledger/` owns deterministic ingestion, annotation, and
  escalation tagging (`needs-athena-review`).
- `skills/athena-ledger/` owns scheduled analysis, anomaly interpretation, and
  human-facing summaries.
- Notification and architecture docs codify Hestia-first wake behavior with
  Athena-later cadence.

### M13 — Hardened deployment & local security (Sprint 15)

Sprint 15 is complete. Production-grade deployment primitives are now available
for home-server operators:

- `deploy/systemd/` — systemd service, sync timer, and DuckDNS timer unit
  files; RUNBOOK.md Section 12 covers installation, `journalctl` usage, and
  drop-in overrides.
- `deploy/docker/` — multi-stage Dockerfile, `docker-compose.yml`, and
  `.dockerignore`; RUNBOOK.md Section 13 covers Docker and LXC paths.
- `CLAW_WEBHOOK_ALLOWED_IPS` / `CLAW_TRUSTED_PROXIES` — server-side webhook
  IP allowlisting with `X-Forwarded-For` resolution; `doctor` reports
  allowlist status; RUNBOOK.md Section 9.6 documents three-layer enforcement.
- `deploy/proxy/` — Caddy mTLS, nginx mTLS, and Authelia OIDC configuration
  examples; RUNBOOK.md Section 14 provides the mTLS walkthrough.
- RUNBOOK.md Section 15 — deployment selection guide with decision tables for
  deployment method and auth hardening pattern.

### M14 — API quality-of-life & skill discovery (Sprint 16)

Sprint 16 is complete. Four focused improvements from the first production run
of the two-agent household:

- **BUG-006** — `PUT /annotations/{transaction_id}` now returns the full
  updated transaction record (same shape as `GET /transactions/{id}`),
  eliminating the need for a follow-up GET.
- **BUG-007** — `GET /categories` and `GET /tags` return the distinct, sorted
  vocabulary of category and tag values already present in annotations, giving
  agents a consistent vocabulary to annotate against.
- **BUG-010** — `GET /spend` accepts an optional `range` parameter
  (`last_month`, `this_month`, `last_30_days`, `last_7_days`) so callers do
  not have to compute and format date pairs for common queries; resolved dates
  are always surfaced in the response.
- **BUG-004** — `sync-skills.sh push` now idempotently injects a `## Skills`
  block (from SKILL.md frontmatter) into each target agent's `TOOLS.md` after
  copying skill files; RUNBOOK.md Section 16 documents the workflow and manual
  fallback.

### M15 — Account labels & enriched spend queries (Sprint 17)

Sprint 17 is complete. Three production gaps from the first M14 deployment
are resolved:

- **BUG-005** — `account_labels` table (idempotent `CREATE TABLE IF NOT
  EXISTS`) with `label` and `description` columns keyed on Plaid account ID.
  `GET /accounts` returns all known accounts LEFT JOINed with label data.
  `PUT /accounts/{account_id}` upserts label data; returns 404 for unknown
  account IDs.
- **BUG-008** — `GET /spend` now accepts `account_id` to restrict aggregation
  to a single account (no JOIN required; direct `plaid_account_id` match).
- **BUG-009** — `GET /spend` now accepts `category` (case-insensitive category
  match against annotations) and `tag` (case-insensitive singular tag match
  via `json_each`). All three new filters are AND-combined with each other
  and with the existing `owner` and `tags` parameters.
- **Skill docs** — Both `hestia-ledger` and `athena-ledger` skill bundles
  updated: `GET /accounts` and `PUT /accounts/{account_id}` added to approved
  API call lists; `GET /spend` new filter params documented; account-scoped
  spend playbook added to Athena's `query_playbooks.md`.

### M16a — Transaction list fixes (out-of-sprint patch)

Two production gaps reported by Athena resolved as a targeted patch against
the M16 codebase:

- **BUG-012** — `GET /transactions` now accepts `range` (`last_month`,
  `this_month`, `last_30_days`, `last_7_days`), matching the behaviour
  already present on `GET /spend`. Explicit `start_date`/`end_date` still take
  precedence when provided alongside `range`. Root cause was a module-ordering
  issue: `_SpendRange` was defined after the route decorator ran, so FastAPI
  silently discarded the parameter. Fix: moved the type alias before
  `list_transactions()` in `server.py`.
- **BUG-013** — `GET /transactions` list results now include a nested
  `annotation` field (`category`, `note`, `tags`, `updated_at`) when an
  annotation exists, or `null` otherwise — field-for-field identical to
  `GET /transactions/{id}`. Previously the LEFT JOIN on `annotations` was used
  only for filtering; columns were dropped at projection time. This eliminates
  the need for per-transaction drill-down calls during initial screening.
- **Skill docs** — `skills/athena-ledger/` and `skills/hestia-ledger/`
  updated to reflect both changes.

### M16 — Spend trends (Sprint 18)

Sprint 18 is complete. Month-over-month spend analysis is now available
without manual stitching:

- **BUG-011** — `GET /spend/trends` returns a plain JSON array of monthly
  bucket objects (oldest → newest), zero-filling months with no qualifying
  transactions. Each bucket contains `month` (YYYY-MM), `total_spend`,
  `transaction_count`, and `partial` (true only on the current in-progress
  month). The `months` parameter (default 6, minimum 1, no upper bound)
  controls the lookback window. All seven filter parameters from `GET /spend`
  (`owner`, `tags`, `category`, `tag`, `account_id`, `view`,
  `include_pending`) are supported for direct comparability.
- **Skill docs** — `skills/athena-ledger/SKILL.md` lists `GET /spend/trends`
  in the approved API calls and documents it under Core analysis workflows
  (section 4). `skills/athena-ledger/checklists/query_playbooks.md` includes
  a "Month-over-month trends" playbook entry (playbook 7). Hestia's skill
  docs are unchanged.

### M17 — Errors visible to OpenClaw (Sprint 19)

Sprint 19 is complete. Ledger warnings and errors are now visible to OpenClaw
agents without tailing logs:

- **`ledger_errors` table** — new SQLite table persists WARNING, ERROR, and
  CRITICAL log records automatically. Rows include `severity`, `logger_name`,
  `message`, `correlation_id`, and `created_at`. Retention policy: rows older
  than 30 days are pruned on each insert.
- **`LedgerDbHandler`** — a `logging.Handler` subclass installed in the
  server's `lifespan()` context manager. Any logger running during server
  operation (background sync, webhook handler, request handler) writes WARNING+
  records to `ledger_errors` automatically — no per-call instrumentation
  required. A `threading.local()` re-entrancy guard prevents infinite
  recursion.
- **`GET /errors`** endpoint — bearer-auth required. Query parameters: `hours`
  (lookback window, minimum 1; `?hours=0` → 422), `min_severity` (`WARNING` or
  `ERROR`), `limit` (max 500), `offset`. Response shape:
  `{ errors, total, limit, offset, since }`. Rows are ordered newest first.
- **`doctor` integration** — `ledger doctor` reports
  `doctor: error-log warn=N error=N (last 24h)` and schema check FAILs if
  `ledger_errors` table is absent.
- **Skill docs** — both `hestia-ledger` and `athena-ledger` skill bundles
  updated with `GET /errors` in approved API calls, concrete pagination
  mechanics (`limit`/`offset`/`total`), and agent-specific usage guidance
  (Hestia: pre-run health check; Athena: proactive error alerting workflow and
  playbook entry).

---

## Upcoming Milestones

### M18 — Split test files

**Focus:** Break up unwieldy test modules for LLM context window compatibility
and general maintainability.

**Goal:** No single test file exceeds ~2 000 lines; each module covers a
focused slice of the surface area; shared fixtures live in `conftest.py`.

**Scope**

- Split `tests/test_server.py` into focused modules:
  - `test_server_transactions.py`
  - `test_server_annotations.py`
  - `test_server_spend.py`
  - `test_server_webhooks.py`
  - (additional splits as line counts warrant)
- Move shared fixtures and helpers into `tests/conftest.py`.
- Audit other test files (e.g. `test_cli.py`, `test_sync.py`) and split any
  that are approaching the threshold.
- Confirm the full quality gate (`ruff format`, `ruff check`, `mypy`, `pytest`)
  passes after the reorganisation with no test regressions.

---

### M19 — Split server.py into routers

**Focus:** Decompose the monolithic `server.py` into a proper FastAPI router
structure before M20 adds the allocations route group.

**Goal:** No single source file dominates the API surface; each router module
is responsible for one domain; the app factory is thin and concerned only with
assembly.

**Proposed module structure:**

```
src/claw_plaid_ledger/
  server.py           # app factory: FastAPI instance, lifespan, middleware
                      # registration, and router inclusion (~50 lines)
  middleware/
    auth.py           # require_bearer_token, HTTPBearer setup
    correlation.py    # CorrelationIdMiddleware
    ip_allowlist.py   # WebhookIPAllowlistMiddleware, _resolve_client_ip,
                      # _ip_in_allowlist
  routers/
    health.py         # GET /health, GET /errors
    transactions.py   # GET /transactions, GET /transactions/{id},
                      # PUT /annotations/{id}
    spend.py          # GET /spend, GET /spend/trends
    accounts.py       # GET /accounts, PUT /accounts/{id},
                      # GET /categories, GET /tags
    webhooks.py       # POST /webhooks/plaid, background sync,
                      # scheduled sync, lifespan helpers
```

**Constraints:**

- Pure internal restructure — zero API behavior change, zero schema change,
  no new functionality.
- All routers use FastAPI's `APIRouter`; assembled in `server.py` via
  `app.include_router()`.
- Quality gate must pass identically before and after.
- The M18 test split should require only import-path updates, not test logic
  changes.

---

### M20 — Allocation Model for Multi-Purpose Transactions

**Goal:** support one Plaid transaction being budgeted across multiple categories without mutating imported transaction data.

#### Problem

Plaid transactions represent settlement events, not necessarily a single budgeting intent. Merchants like Amazon commonly bundle unrelated purchases into one bank transaction, which breaks one-transaction / one-category assumptions.

#### Design

- Keep `transactions` as immutable imported Plaid data.
- Introduce `allocations` as the budgeting layer.
- Each Plaid transaction will map to one or more allocation rows.
- A normal transaction is represented by exactly one allocation.
- A mixed-purpose transaction (for example, an Amazon order containing household, toiletries, and kids items) can have multiple allocations.
- `annotations` remains transaction-level metadata only and no longer stores category/tag information.

#### Invariants

- Every categorized transaction must be represented through `allocations`.
- Sum of allocation amounts for a transaction must equal the transaction amount.
- Plaid-synced transaction rows remain the source of truth for imported banking data.
- Allocation logic is independent from duplicate-account canonicalization.

#### Deliverables

- Add `allocations` table keyed to `plaid_transaction_id`.
- Migrate existing categorized transactions so each one gets a single allocation row.
- Remove category/tag ownership from `annotations`.
- Update transaction detail flows to read/write allocations.
- Update reporting, budgeting, and category summaries to read from allocations only.
- Update `skills/athena-ledger/` and `skills/hestia-ledger/` skill bundles to reflect the new `allocations`-based categorization model: remove references to category/tag fields on `annotations`, document the `allocations` table and any new API endpoints, and update query playbooks accordingly.

#### Acceptance criteria

- Existing categorized transactions continue to work after migration through their single allocation row.
- A single Plaid transaction can be decomposed into multiple category allocations.
- Reports and rollups use allocations as the sole source of budgeting truth.
- Plaid import and sync logic does not change its ownership boundaries.

---

### M21 — Manual Allocation Editing

**Goal:** make multi-allocation transactions usable before any receipt automation exists.

#### Deliverables

- Add API and/or CLI support for creating, updating, and deleting allocations for a transaction.
- Show raw transaction totals alongside allocation totals for validation.
- Prevent saving allocations whose amounts do not reconcile to the parent transaction.
- Update `skills/athena-ledger/` and `skills/hestia-ledger/` skill bundles with the new allocation editing endpoints, validation behavior, and relevant playbook entries.

#### Acceptance criteria

- A user can take one imported transaction and allocate it across multiple categories.
- Validation prevents under- or over-allocation.
- Unmodified transactions still behave as a single-allocation case.

---

### M22 — Receipt-Assisted Amazon Allocation

**Goal:** use forwarded receipts to propose allocations for mixed Amazon purchases.

#### Deliverables

- Parse forwarded Amazon receipts into candidate line items.
- Map receipt totals back to the imported Plaid transaction.
- Convert parsed line items into proposed allocations.
- Allow review/edit before final save when needed.
- Update `skills/athena-ledger/` and `skills/hestia-ledger/` skill bundles to document receipt-assisted allocation flows, any new endpoints or CLI commands introduced, and when agents should use or surface proposed allocations.

#### Notes

- Receipt parsing proposes allocation structure; it does not replace immutable Plaid transaction data.
- Automation should build on the allocation model, not invent a second categorization path.

#### Acceptance criteria

- An Amazon transaction can be expanded into multiple allocations derived from receipt contents.
- Users can correct allocations before committing them.
- Final saved budgeting data still lives only in `allocations`.

---

### M23 — Remove Annotations Table

**Goal:** simplify the data model by eliminating the now-redundant `annotations`
table after the allocation migration is complete.

**Rationale:** The project is intentionally collapsing from a two-table semantic
model (`annotations` + `allocations`) into a single semantic table. In practice,
transaction-level metadata stored in `annotations` (category, tags, notes) has not
been pulling its weight as a separate layer — the same information belongs in
`allocations`, which is already the authoritative budgeting surface. Keeping both
tables creates dual-write complexity with no benefit. After M20–M22 fully migrate
that data, `annotations` becomes dead weight and should be removed entirely.

#### Deliverables

- Migrate any remaining `annotations.note` data into `allocations`.
- Remove all reads/writes that depend on `annotations`.
- Delete the `annotations` table and related migration compatibility code.
- Update API, CLI, and reporting paths to use `allocations` as the only
  semantic/budgeting layer.

#### Acceptance criteria

- No production code depends on `annotations`.
- Transaction categorization, tags, and notes are stored only in `allocations`.
- Transaction sync/import logic remains unchanged and Plaid data remains immutable.
- The schema is simpler: raw financial events in `transactions`, semantic budgeting
  data in `allocations`.

---

## Deferred / Unscheduled

### `doctor` auto-remediation

**Focus:** Reduce manual maintenance and recovery toil.

**Goal:** Expand diagnostics into safe, explicit remediation workflows.

**Scope**

- Add `ledger doctor --fix` style flows for common recoverable issues:
  - Missing/incomplete `items.toml` bootstrap
  - Stale or pending migrations
  - File permission and path readiness problems
- Add pre-sync health checks before `sync --all` with clear, actionable errors.
- Preserve dry-run and audit output so operators can review planned fixes.

**Design questions for PM/user**

- Should auto-fix be interactive by default, or non-interactive with
  `--yes/--force` semantics?
- What risk level is acceptable for auto-remediation (config edits only vs.
  database mutations)?

### Markdown export

Human-readable transaction summaries written to the OpenClaw workspace.
Originally planned for M3. Not urgent now that agents have a proper query
API, but may be useful for contexts where tool calls are unavailable or for
manual review workflows. Revisit after M11.

### Per-agent token scoping

If multiple OpenClaw agents with different trust levels need ledger access,
replace `CLAW_API_SECRET` with a small token table mapping tokens to
permission scopes (read-only vs. read-write annotations). Not needed while
Hestia is the only consumer.

### Budget rule engine

Explicit rules for recurring spend categories, monthly targets, and
over-budget alerts. The annotation layer may make this unnecessary for
day-to-day use; revisit once Hestia has several months of annotation history
to assess whether rule-based guardrails add value over conversational
queries.

### Operator review queue for ambiguous identity matches

If automatic account or transaction source-precedence confidence is low,
surface a small review queue rather than silently guessing. This can remain manual until the
household has enough real production history to reveal the weird edge cases.

### Parallel multi-institution sync

M6 syncs institutions sequentially. If sync latency becomes a problem with
5+ items, parallel execution via `asyncio` or worker threads is a natural
extension.
