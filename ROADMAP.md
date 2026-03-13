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

---

## Upcoming Milestones

### M12 — Hestia skill definition

**Focus:** Finalize agent operating contract on top of canonical ledger logic.

**Goal:** Publish Hestia `SKILL.md` guidance that reinforces deterministic data
usage, anomaly discovery, and annotation hygiene.

**Scope**

- Created as separate files for easy copying into a new OpenClaw SKILL project.
- Define Hestia API usage constraints and guardrails.
- Add prompting guidance for owner-aware summaries and anomaly review.
- Document “orphaned transactions” and discrepancy workflows where Hestia acts
  as a safety net, not a source-precedence override.
- Align architecture docs with the agent role boundary.

### M12a - Redirect
We were trying to cram an entire finance department's worth of responsibilities into a single agent, mixing cheap, high-volume data entry tasks with high-level cognitive analysis. Splitting them into a two-agent pipeline completely fixes the human-in-the-loop fatigue you were worried about.

Here is how we should redirect Sprint 13 and the architecture to support the Hestia/Athena split:

### 1. The New Agent Boundaries

* **Hestia (The Bookkeeper):** Wakes up when the Plaid webhook fires. She runs on an inexpensive, fast model. Her only job is to query `GET /transactions` for anything missing an annotation, look at the raw data, and `PUT /annotations/{id}` to categorize it. She never talks to you.
* **Athena (The Analyst):** Wakes up either on a schedule (e.g., weekly) or when Hestia explicitly asks her to review something weird. She runs on a smarter model. She handles the `GET /spend` rollups, tag reviews, anomaly detection, and actually formats the summaries for you.

### 2. Fixing the Communication and Notifications

Right now, `notifier.py` hardcodes a message that says: *"Plaid sync complete... Review new transactions and annotate as appropriate"*.

To fix the communication flow without adding complex messaging overhead, we can use an **event-driven database handoff**:

1. **The Server Wake:** `notifier.py` wakes Hestia silently in the background.
2. **Hestia's Run:** Hestia categorizes the new batch. If she finds something she is highly uncertain about (an anomaly, an orphaned transaction, a massive unexpected bill), she applies a specific tag via the API, like `"needs-athena-review"`.
3. **The Handoff:** Once Hestia finishes her batch, if OpenClaw supports agent-to-agent triggering, she can fire a wake command to Athena. If not, Athena can simply run on a cron schedule, querying `GET /transactions?tags=needs-athena-review` to see what Hestia flagged, alongside pulling her normal weekly `GET /spend` summaries to present to you.

### 3. Redirection Plan for Sprint 13 (M12)

We should scrap the current `SKILL.md` tasks and replace them with a clean split.

**Task 1: Strip and refocus the Hestia skill bundle**

* Remove all mention of household summaries, anomalies, and reporting.
* Define her strict loop: Fetch unannotated `GET /transactions`, apply logic, write `PUT /annotations/{id}`.
* Define her escalation rule: When confidence is low, tag it `"needs-athena-review"` and move on.

**Task 2: Create the Athena skill bundle (`skills/athena-ledger/`)**

* Give Athena the analytical playbooks.
* Define how she queries `GET /spend` for date windows and filters by tags.
* Define her anomaly-review playbook (looking at Hestia's flagged transactions).

**Task 3: Update `notifier.py` and `ARCHITECTURE.md**`

* Change the default `OPENCLAW_HOOKS_AGENT` configuration to ensure it explicitly targets Hestia as the ingestion worker.
* Change the notification prompt string to remove the implication that a human needs to read the OpenClaw alert.

### M13 — Hardened deployment & local security

**Focus:** Durable home-server operations with explicit local trust boundaries.

**Goal:** Transition from ad-hoc `ledger serve` sessions to repeatable,
production-like local deployment patterns.

**Scope**

- Provide official `systemd` service and timer templates for Linux/Proxmox.
- Offer container deployment examples (Docker/LXC) for self-hosted setups.
- Add local-network auth hardening options (mTLS or OIDC-style front-proxy
  integration).
- **Webhook ingress hardening:**
  - Document router-level IP allowlisting for Plaid's published webhook source
    ranges so the `/webhooks/plaid` endpoint is not reachable from arbitrary
    internet hosts.
  - Evaluate and optionally implement Plaid's JWKS-based webhook verification
    (rotating key JWT signatures) as an alternative or complement to the current
    static HMAC-SHA256 signing-secret approach.  See
    [Plaid webhook verification docs](https://plaid.com/docs/api/webhooks/webhook-verification/).
    The static HMAC approach (M3) is sufficient for most operators; JWKS
    verification removes the need to store a long-lived signing secret.

**Design questions for PM/user**

- Which deployment target is primary for support burden: `systemd` or container?
- Is local single-user bearer auth still acceptable, or is multi-device auth now
  a release requirement?
- For webhook hardening: prefer IP allowlisting (network-layer, no code change),
  JWKS verification (code change, eliminates stored secret), or both?

### M14 — `doctor` auto-remediation

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
