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

---

## Upcoming Milestones

### M7 — Production operations and runbook

**Focus:** Operational readiness before any household production onboarding.

**Goal:** A complete, trustworthy runbook for moving from sandbox to live bank
signals without surprises — production Plaid access, cost model clarity, token
lifecycle handling, OAuth/manual Link expectations, and recovery procedures.

**Scope**

- Production migration runbook (Markdown, committed) covering:
  - Plaid production-access prerequisites and dashboard checklist
  - cost model (billable events tied to Link/item creation, not sync frequency)
  - access-token lifecycle (persistence, revocation, forced re-auth edge cases)
  - sandbox/production isolation guarantees and operator safety checks
  - manual migration and first-live-sync validation checklist
  - backup/recovery guidance for SQLite + config secrets
- `ledger doctor --production-preflight` checks required live config before
  production credentials are used

**Not in scope**

- Automatic Link/OAuth automation (manual only)
- Multi-item household ingestion workflows (handled in M8)

---

### M8 — Multi-item management

**Focus:** Household onboarding and reliable data ingress.

**Goal:** Onboard the real household structure with explicit multi-item
configuration and sync controls.

**Scope**

- Implement operator-facing manual Link flow for production token capture
- Finalize `items.toml` household structure with real owner/institution examples
  (Aron/Michelle, USAA/AmEx)
- Ensure `ledger sync --all` is the standard household ingestion path
- Add item inspection/health visibility sufficient for daily operations and
  relink triage

**Not in scope**

- Canonical duplicate suppression across overlapping items (handled in M9)

---

### M9 — Canonical household views (source precedence)

**Focus:** Solve joint-account overlap deterministically.

**Goal:** Preserve all raw Plaid records while exposing one canonical
household view that suppresses redundant overlap by configuration-driven,
explainable rules.

**Design principles**

- Deterministic account-level mapping (no fuzzy-first dedupe engine)
- Source precedence and account aliasing live in configuration (`items.toml`)
- Raw ingestion remains complete; suppression happens only in canonical
  query/view layers
- Prefer primary-cardholder visibility for shared credit-card institutions
  (e.g., AmEx) to avoid authorized-user blind spots
- Every suppression remains auditable via provenance metadata

**Scope**

- Rename prior “deduplication” effort to **Household identity and Source
  Precedence**
- Define canonical account aliasing and precedence rules in config
- Update API defaults to canonical household transactions, with explicit access
  to raw/source records when needed
- Persist duplicate-suppression provenance (winner source + suppressed sources)
- Add operator-review path for ambiguous or orphaned overlaps

**Not in scope**

- Heuristic-heavy fuzzy matching as the primary strategy
- Full bookkeeping transfer reconciliation

---

### M10 — Multi-item automation

**Focus:** Automated maintenance for the new household architecture.

**Goal:** Webhook-triggered and background sync flows operate correctly across
all configured household items.

**Scope**

- Route Plaid webhooks to the correct configured item
- Remove single-item assumptions from automatic sync paths
- Preserve idempotent item-scoped sync with overlap-safe execution
- Ensure notifications/logging include item + owner context

**Not in scope**

- Institution-specific webhook customization beyond robust routing

---

### M11 — Hestia skill definition

**Focus:** Agent-led financial collaboration on top of deterministic household
ledger logic.

**Goal:** Hestia validates and collaborates with the canonical ledger, with
special emphasis on anomaly discovery rather than primary deduplication.

**Scope**

- Define Hestia `SKILL.md` and operating constraints for ledger API usage
- Prompting guidance for household analytics, annotation hygiene, and owner-aware
  summaries
- Add an “orphaned transactions” review workflow where Hestia flags anomalies
  missed by deterministic source-precedence rules
- Update architecture docs to reflect Hestia as safety net, not dedupe engine

**Not in scope**

- Automated budget enforcement
- Multi-user authorization expansion

---

## Priority order

1. **M7 (Ops Foundation)** → **M8 (Data Ingress)** → **M9 (Canonical Logic)** →
   **M10 (Automation)** → **M11 (Agent Integration)**.

## Deferred / Unscheduled

### systemd deployment

`ledger serve` + scheduled sync timers on the OpenClaw machine remain valuable,
but are no longer on the critical path for the M7→M11 household production
pivot. Revisit after canonical household views (M9) are stable.

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

---

## Deferred / Unscheduled additions

### Transfer detection and internal movement suppression

Once canonical accounts exist, add optional logic to identify likely internal
transfers between household accounts so "money moved from checking to card
payment" does not read like new spending. Defer until raw/canonical account
identity and duplicate handling are stable.

### Operator review queue for ambiguous identity matches

If automatic account or transaction dedupe confidence is low, surface a small
review queue rather than silently guessing. This can remain manual until the
household has enough real production history to reveal the weird edge cases.

### Parallel multi-institution sync

M6 syncs institutions sequentially. If sync latency becomes a problem with
5+ items, parallel execution via `asyncio` or worker threads is a natural
extension.
