# Roadmap

## Upcoming Milestones

### M26 — Plaid Required Attestations (due 2026-09-07)

**Goal:** Complete all eleven Plaid compliance attestations before the
2026-09-07 deadline to maintain production API access.

**Plaid dashboard:** https://dashboard.plaid.com/settings/company/compliance?tab=dataSecurity

**Rationale:** Plaid requires these attestations as part of their production
access security review. Each item below must be formally attested in the Plaid
dashboard. Most require documented policies or implemented controls to exist
first.

#### Attestations

- **RBAC** — Attest that role-based access control is implemented.
- **MFA (consumer-facing)** — Attest that MFA is implemented on the
  consumer-facing application where Plaid Link is deployed.
- **EOL software management** — Attest that end-of-life software is monitored
  and that update/EOL management practices are documented in policy.
- **Information Security Policy (ISP)** — Attest that an ISP has been created.
- **Privacy policy** — Attest that a privacy policy has been published.
- **Periodic access reviews** — Attest that access reviews and audits are
  performed periodically.
- **Centralized IAM** — Attest that centralized identity and access management
  solutions are in place.
- **MFA (internal systems)** — Attest that MFA is implemented on internal
  systems that store or process consumer data.
- **Vulnerability patching SLA** — Attest that identified vulnerabilities are
  patched within a defined SLA.
- **Zero trust architecture** — Attest that a zero trust access architecture
  is implemented.
- **Secure tokens and certificates** — Attest that secure tokens and
  certificates are used for authentication.

#### Acceptance criteria

- All eleven attestations submitted in the Plaid dashboard before 2026-09-07.
- Any required supporting documents (ISP, privacy policy, access review records)
  are drafted and stored before attesting.

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

### CSV export for financial data

Export transaction and allocation data as CSV for use in spreadsheet tools
and manual review workflows.

**Scope**

- CLI command (e.g. `ledger export`) that writes a CSV to stdout or a named
  file.
- Supports the same filter parameters as `GET /transactions` (date range,
  account, owner, category, view).
- Each row represents one allocation (matching the API's one-row-per-allocation
  semantics introduced in M20).
- Columns include: transaction date, merchant, transaction amount, allocation
  amount, category, tags, note, account id, owner.

**Design questions for PM/user**

- Should an API endpoint (`GET /export/csv`) be added alongside the CLI, or
  is CLI-only sufficient?
- What file-naming convention should the CLI use for file output vs. stdout?

---

### Parallel multi-institution sync

M6 syncs institutions sequentially. If sync latency becomes a problem with
5+ items, parallel execution via `asyncio` or worker threads is a natural
extension.

---

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
  `allocation_count`, and `partial` (true only on the current in-progress
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

### M18 — Split test files (Sprint 20)

Sprint 20 is complete. The two monolithic test files have been broken into
focused modules with no test regressions:

- `tests/test_cli.py` (1 725 lines) split into four files by CLI command:
  `test_cli_doctor.py`, `test_cli_items.py`, `test_cli_link.py`,
  `test_cli_sync.py`.
- `tests/test_server.py` (5 249 lines) split into twelve focused modules
  covering endpoints, middleware, webhooks, scheduling, auth, correlation
  logging, and IP allowlisting.
- Shared fixtures and seed helpers promoted to `conftest.py` and `helpers.py`;
  no helpers are duplicated across files.
- All test files are under the 2 000-line threshold.
- Full quality gate (`ruff format`, `ruff check`, `mypy`, `pytest`) passes
  with identical test counts before and after.

### M19 — Split server.py into routers (Sprint 21)

Sprint 21 is complete. The 1 054-line `server.py` monolith has been
decomposed into a proper FastAPI router structure:

- **`middleware/`** package: `auth.py` (bearer token), `correlation.py`
  (`CorrelationIdMiddleware`), `ip_allowlist.py`
  (`WebhookIPAllowlistMiddleware`).
- **`routers/`** package: `health.py` (`GET /health`, `GET /errors`),
  `transactions.py` (`GET /transactions`, `GET /transactions/{id}`,
  `PUT /annotations/{id}`), `spend.py` (`GET /spend`, `GET /spend/trends`),
  `accounts.py` (`GET /accounts`, `PUT /accounts/{id}`, `GET /categories`,
  `GET /tags`), `webhooks.py` (`POST /webhooks/plaid`, `_background_sync`,
  scheduled-sync helpers, `lifespan`), `utils.py` (shared date-range
  helpers and `_strict_params`).
- **`server.py`** is now a thin app factory (~50 lines): no route handlers,
  models, or helpers; only imports, middleware registration, and router
  inclusion.
- **BUG-014** resolved: `_strict_params` dependency wired into every
  parameterised GET endpoint (`GET /errors`, `GET /transactions`,
  `GET /spend`, `GET /spend/trends`). Unknown query parameters return
  HTTP 422 with `"unrecognized"` and `"valid_parameters"` keys.
  `AnnotationRequest` and `AccountLabelRequest` both have
  `extra="forbid"` on their Pydantic models.
- Pure structural refactor — zero API behaviour change, zero schema change.
- Full quality gate passes with identical test counts (458 tests).

### M20 — Allocation Model for Multi-Purpose Transactions (Sprint 22)

Sprint 22 is complete. The `allocations` table is now the budgeting layer:

- **`allocations` table** — `id`, `plaid_transaction_id`, `amount`, `category`,
  `tags`, `note`, `created_at`, `updated_at`. No UNIQUE constraint on
  `plaid_transaction_id` (multi-allocation support added in M21).
- **`upsert_transaction()` seeding** — every new transaction automatically gets a
  blank allocation row (`amount = transaction.amount`, semantic fields null).
  A startup migration backfills allocations for any transaction that lacks one.
- **Double-write** — `PUT /annotations/{id}` writes to both `annotations` and
  `allocations`. The `annotations` table is preserved for backward compatibility
  and decommissioned in M22.
- **Response shape** — all transaction endpoints (`GET /transactions`,
  `GET /transactions/{id}`, `PUT /annotations/{id}`) now return an `allocation`
  key (never null) instead of `annotation`. The `allocation` object contains
  `id`, `amount`, `category`, `tags`, `note`, and `updated_at`.
- **Spend uses allocation amounts** — `GET /spend` and `GET /spend/trends` sum
  `allocations.amount`. Numerically identical to transaction amounts for M20;
  future-proofs multi-allocation math.
- **Vocabulary from allocations** — `GET /categories` and `GET /tags` draw from
  `allocations` (not `annotations`).
- **Skill bundles updated** — both `skills/hestia-ledger/` and
  `skills/athena-ledger/` reflect the allocation model; no skill file references
  `annotation.category`, `annotation.tags`, or `annotation.note`.

### M21 — Manual Allocation Editing (Sprint 23)

Sprint 23 is complete. Multi-allocation transactions are now fully usable:

- **`PUT /transactions/{id}/allocations`** — atomically replaces all
  allocations for a transaction with a validated set. Amounts are
  auto-corrected within $1.00; returns HTTP 422 if the difference exceeds
  $1.00. This is the primary write surface for all allocation edits.
- **Response shape** — `GET /transactions/{id}` and
  `PUT /transactions/{id}/allocations` return `"allocations": [...]` (array,
  never null). The list endpoint (`GET /transactions`) retains
  `"allocation": {...}` per row.
- **`PUT /annotations/{id}` restriction** — returns HTTP 409 for split
  transactions; single-allocation transactions continue to work as before.
- **`allocation_count`** — `GET /spend` and `GET /spend/trends` rename the
  count field from `transaction_count` to `allocation_count`.
- **CLI** — `ledger allocations show <id>` displays the current allocation
  state; `ledger allocations set <id> --file <path>` replaces allocations
  from a JSON file (or stdin with `--file -`).
- **Skill bundles** — both `skills/hestia-ledger/` and `skills/athena-ledger/`
  updated with the new endpoint, response shapes, and allocation-first
  write guidance.

### M22 — On-demand Plaid Refresh (Sprint 24)

Sprint 24 is complete. Operators can now trigger an immediate Plaid transaction
refresh from the CLI:

- **`PlaidClientAdapter.refresh_transactions(access_token)`** — thin adapter
  method wrapping `/transactions/refresh`. Fire-and-forget; returns `None` on
  success; raises `PlaidTransientError` for HTTP 429/5xx and network errors;
  raises `PlaidPermanentError` for other 4xx errors.
- **`ledger refresh`** — calls `/transactions/refresh` for the singleton
  `PLAID_ACCESS_TOKEN` item; prints `refresh: OK` on success.
- **`ledger refresh --item <id>`** — calls `/transactions/refresh` for the
  named item from `items.toml`; prints `refresh[<id>]: OK` on success.
- **`ledger refresh --all`** — calls `/transactions/refresh` for every item in
  `items.toml`; prints per-item results and a `refresh --all: N items
  refreshed, M failed` summary line; exits 1 if any item failed.
- **Exit-code conventions** match `ledger sync`: missing config exits 2, adapter
  errors exit 1, `--item` and `--all` together exits 2.
- No API endpoint added, no schema changed, no skill bundles modified.

### M23 — Remove Annotations Table (Sprint 25)

Sprint 25 is complete. The `annotations` table and every piece of code that read or
wrote it have been removed:

- **Schema** — `CREATE TABLE annotations` block removed from `schema.sql`.
  `DROP TABLE IF EXISTS annotations` runs idempotently on every startup, cleaning
  up any live database that still carries the old table.
- **DB layer** — `AnnotationRow`, `upsert_annotation()`, and `get_annotation()`
  removed from `db.py`. All migration and backfill code (migration_stmts loop,
  annotation backfill patches) removed from `initialize_database()`.
- **API** — `PUT /annotations/{transaction_id}` endpoint removed entirely.
  `AnnotationRequest` Pydantic model removed. The endpoint now returns HTTP 404.
- **Tests** — `tests/test_server_annotations.py` deleted. All annotation-specific
  DB tests removed. Category/tag seeding in `test_server_categories.py` migrated
  to use `allocations` directly.
- **Skill bundles and docs** — All markdown, skill files, and proxy config examples
  updated to remove `annotations` references. `annotation_write_checklist.md`
  merged into `allocation_write_checklist.md` and deleted.
- The data model is now simpler: raw financial events in `transactions`;
  all semantic/budgeting data exclusively in `allocations`.

### M23a — Skill exec wrapper for ledger API (Sprint 26)

Sprint 26 is complete. OpenClaw skill HTTP calls now route through a dedicated
wrapper that is compatible with exec approvals:

- **BUG-019 resolved** — added `scripts/ledger-api`, a shared wrapper that
  sources `~/.openclaw/.env` in-process, enforces `CLAW_API_SECRET`, defaults
  `CLAW_LEDGER_URL` to `http://127.0.0.1:8000`, and forwards extra curl args.
- **Deploy integration** — `scripts/deploy-local.sh` installs the wrapper to
  `/usr/local/bin/ledger-api` during local deploys.
- **Skill bundles updated** — both `skills/hestia-ledger/` and
  `skills/athena-ledger/` now declare `binaries: [ledger-api]`, use
  `doctor: 'ledger-api /health'`, and prohibit direct `curl` usage in normal
  API workflows.
- **Operations docs updated** — RUNBOOK post-upgrade cleanup now documents
  removing stale `/usr/bin/curl` allowlist entries and optional
  `openclaw.json` simplification.

### M25 — Scheduled Sync / Webhook Retirement (Sprint 28)

Sprint 28 is complete. Inbound webhook infrastructure is retired in favor of
a reliable outbound pull cadence:

- **Webhook gating** — `CLAW_WEBHOOK_ENABLED` env var (default `false`); when
  disabled, `POST /webhooks/plaid` returns HTTP 404. Existing webhook behavior
  preserved behind the flag for operators who opt in.
- **`--notify` CLI flag** — `ledger sync --notify` (compatible with `--all` and
  `--item`) calls `notify_openclaw()` after each successful sync that produces
  changes. This is now the primary agent-wake mechanism.
- **Systemd timer as primary sync** — timer defaults to 4×/day; service
  ExecStart includes `--all --notify`. Hourly override documented as a
  `systemctl edit` drop-in.
- **Doctor dual-enablement warning** — `ledger doctor` reports webhook status
  and warns when both `CLAW_WEBHOOK_ENABLED` and `CLAW_SCHEDULED_SYNC_ENABLED`
  are true simultaneously.
- **Deprecation notices** — webhook, DuckDNS, Caddy, and port-forward sections
  in ARCHITECTURE.md and RUNBOOK.md marked deprecated with BUG-018 context.
  In-process fallback loop (`CLAW_SCHEDULED_SYNC_ENABLED`) marked deprecated.
- **Skill bundles updated** — both Hestia and Athena skill docs reflect
  timer-driven sync and `--notify` as the wake mechanism.

### M24 — Batch Allocation Updates & Uncategorized Transaction Query (Sprint 27)

Sprint 27 is complete. Hestia and Athena now have dedicated queue endpoints,
and Hestia can update multiple single-allocation transactions in one request:

- **`GET /transactions/uncategorized`** — returns only rows where
  `allocation.category IS NULL`; supports the full `GET /transactions` filter
  set and strict unknown-parameter rejection.
- **`GET /transactions/splits`** — returns all allocation rows belonging to
  split transactions (allocation count > 1), with the same filters and
  pagination behavior as `GET /transactions`.
- **`POST /transactions/allocations/batch`** — accepts a JSON array of
  `{transaction_id, category?, tags?, note?}` items; processes each item
  independently and returns `{succeeded, failed}` with collect-all-errors
  semantics.
- **Batch replace semantics** — omitted semantic fields are explicitly cleared
  (`NULL`); callers must include all fields they want to retain.
- **Split transaction handling** — split transactions are rejected in `failed`
  with a directive to use `PUT /transactions/{id}/allocations`.
- **Skill bundles updated** — both Hestia and Athena skills now document
  uncategorized/split queue workflows and batch update usage.


---
