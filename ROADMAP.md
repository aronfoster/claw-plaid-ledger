# Roadmap

## Human Questions / Goals

### Errors Visible to OpenClaw
When claw-plaid-ledger logs warnings or errors, make the existence of those clear to OpenClaw so OpenClaw can alert users.

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

---

## Upcoming Milestones

### M10 — Automation & connectivity

**Focus:** Reliable background operations and external reachability.

**Goal:** Move from manually triggered sync patterns to webhook-first ingestion
with deterministic item routing and explicit OpenClaw poke behavior.

**Scope**

- Route Plaid webhooks to the correct configured item in a multi-item household.
- Leave single-item fallback in automatic sync paths but prioriotize multi-item.
- Clarify and codify runtime behavior:
  - **Webhooks = primary change trigger**
  - **Scheduled sync = fallback/recovery only**
    - Flag to enable schedule sync as fallback (24 hours no updates)
  - **OpenClaw poke = post-sync notification behavior**
    - Poke on every transaction update for now
- Add DNS setup guidance and automation hooks (DuckDNS) needed to
  maintain a stable webhook URL.

### M11 — Advanced agent API & logging

**Focus:** Richer machine-usable analytics plus operational observability.

**Goal:** Let Hestia answer common finance questions through first-class
endpoints and make runtime troubleshooting possible from logs alone.

**Scope**

- Add agent-focused API capabilities:
  - Total spend endpoints (date-window and tag-aware)
  - Include/exclude pending controls
  - Server-side filtering by tags and dates
  - Search over notes/memo fields
- Introduce structured INFO/DEBUG logging conventions across CLI, sync, and API
  layers with correlation IDs for request/sync tracing.

**Design questions for PM/user**

- Should spend totals be pre-taxonomy (raw categories only) or annotation-aware
  (agent/human tags take precedence)?
- Should note-search be exact, substring, or full-text indexed search?
- Should DEBUG logs include raw webhook payloads by default, or redact-by-default
  with an explicit unsafe debug flag?

### M12 — Transfer detection & movement suppression

**Focus:** Ledger hygiene for household-level spend accuracy.

**Goal:** Detect likely internal money movement so transfers do not inflate
spending metrics.

**Scope**

- Identify transfer candidates across household accounts using amount/date/account
  heuristics.
- Mark transfer-linked rows with suppression metadata while preserving full raw
  visibility and auditability.
- Expose transfer status in API responses so agent summaries can include/exclude
  movement explicitly.

**Design questions for PM/user**

- Should suppression default to automatic when confidence is high, or always
  require operator confirmation?
- How should partial matches be handled (fees, timing offsets, split transfers)?
- Do we want a “review queue” UX now, or postpone until ambiguous cases appear
  in production?

### M13 — Hestia skill definition

**Focus:** Finalize agent operating contract on top of canonical ledger logic.

**Goal:** Publish Hestia `SKILL.md` guidance that reinforces deterministic data
usage, anomaly discovery, and annotation hygiene.

**Scope**

- Define Hestia API usage constraints and guardrails.
- Add prompting guidance for owner-aware summaries and anomaly review.
- Document “orphaned transactions” and discrepancy workflows where Hestia acts
  as a safety net, not a source-precedence override.
- Align architecture docs with the agent role boundary.

### M14 — Hardened deployment & local security

**Focus:** Durable home-server operations with explicit local trust boundaries.

**Goal:** Transition from ad-hoc `ledger serve` sessions to repeatable,
production-like local deployment patterns.

**Scope**

- Provide official `systemd` service and timer templates for Linux/Proxmox.
- Offer container deployment examples (Docker/LXC) for self-hosted setups.
- Add local-network auth hardening options (mTLS or OIDC-style front-proxy
  integration).

**Design questions for PM/user**

- Which deployment target is primary for support burden: `systemd` or container?
- Is local single-user bearer auth still acceptable, or is multi-device auth now
  a release requirement?

### M15 — `doctor` auto-remediation

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

## Deferred / Unscheduled

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
