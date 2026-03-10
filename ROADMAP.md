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

---

## Upcoming Milestones

### M5 — OpenClaw notification

**Goal:** After a webhook-triggered sync, wake Hestia when there are
transactions worth reviewing. The mechanism is already documented in
OpenClaw: a `POST` to its local webhook endpoint.

**Scope**

- After a successful background sync where `added + modified + removed > 0`,
  call OpenClaw's `POST /hooks/agent` endpoint with a message summarising the
  change counts and a prompt to review for annotation
- Zero-change syncs must not trigger a notification (the log warning already
  exists; the notification gate lives in the same place)
- New config variables:
  - `OPENCLAW_HOOKS_URL` — defaults to `http://127.0.0.1:18789/hooks/agent`
  - `OPENCLAW_HOOKS_TOKEN` — required when notification is enabled; if unset,
    notification is skipped with a warning rather than crashing
  - `OPENCLAW_HOOKS_AGENT` — name of the agent to invoke (e.g. `Hestia`);
    defaults to `Hestia`
  - `OPENCLAW_HOOKS_WAKE_MODE` — `now`, since agents do not have heartbeats configured
- `doctor` command extended to report whether notification is configured
- `ARCHITECTURE.md` updated with the full integration pattern

**Documentation**

- https://openclawcn.com/en/docs/automation/webhook/

**Example payload**

```json
{
  "message": "Plaid sync complete: 3 added, 1 modified. Review new transactions and annotate as appropriate.",
  "name": "Hestia",
  "wakeMode": "now"
}
```

**Not in scope**

- Per-transaction detail in the notification payload (Hestia queries the
  API herself)
- Notification routing to messaging channels (WhatsApp, Telegram, etc.) —
  that is OpenClaw configuration, not ledger configuration

---

### M6 — Multi-institution management

**Goal:** Support the full household account structure — multiple institutions,
multiple owners — without requiring manual `.env` switching per sync run.

**Household structure (expected)**

A typical household will have approximately 4–5 Plaid items:

| Item ID (suggested) | Institution | Owner |
|---|---|---|
| `bank-alice` | First Bank | alice |
| `bank-bob` | First Bank | bob |
| `bank-shared` | First Bank | shared |
| `card-alice` | Card Co | alice |
| `card-bob` | Card Co | bob |

The exact structure depends on which institutions are linked.
Item IDs are operator-assigned strings in config, not Plaid identifiers.

**Scope**

- `ledger sync --all` command that reads a multi-item config and syncs every
  item in sequence, each with its own cursor and access token
- Multi-item config format: `~/.config/claw-plaid-ledger/items.toml` listing
  item ID, access token env var name, and optional owner tag
  (`shared` | `alice` | `bob` or any string)
- `owner` tag stored in `sync_state` and surfaced on accounts so Hestia can
  answer household-scoped vs. individual-scoped queries without schema changes
  to `transactions`
- `ledger sync --item <id>` retains the existing single-item invocation
  pattern for scripting and debugging
- `doctor` extended to report per-item sync state and last-synced timestamps
- Existing single-`CLAW_PLAID_LEDGER_ITEM_ID` path remains valid for simple
  setups; `items.toml` is additive

**Design decision to record**

Owner scoping is a naming convention on `item_id` (e.g. `card-alice`,
`card-bob`, `bank-shared`), not a new `transactions` column. Hestia filters
by account via the `account_id` query parameter after learning which accounts
belong to which item. This keeps the sync engine and API unchanged.

**Not in scope**

- Parallel/concurrent sync across items
- Per-item notification routing

---

### M7 — Production Plaid migration

**Goal:** A complete, trustworthy runbook for moving from sandbox to live
bank data without surprises — costs, token lifecycle, OAuth flow, and
dev/prod isolation all documented before any production credentials are
created.

**Key facts to encode in the runbook**

*Cost model:* Plaid bills per linked item per month, not per sync call.
`/transactions/sync` (webhook-triggered) has no per-call charge. The only
action that creates a new billable event is running the Link flow to obtain
a new access token. Existing access tokens survive server restarts, database
recreation, and OS reimages — as long as the token value is preserved in
config, the item remains live and no re-linking is required.

*Token lifecycle:* Access tokens do not expire under normal operation. They
are invalidated only if explicitly revoked via the Plaid dashboard, or if
the institution forces re-authentication (rare, institution-dependent).

*Dev/prod isolation:* Sandbox tokens are cryptographically scoped to Plaid's
sandbox environment and will be rejected by production endpoints, and vice
versa. A development machine configured with `PLAID_ENV=sandbox` and a
sandbox access token cannot reach or accidentally ingest real transaction
data regardless of network access.

*OAuth institutions:* Many banks and credit card providers use OAuth-based
Plaid Link. The Link flow requires a browser and interactive login at the
institution's website. This must be performed on a machine with a GUI (the
OpenClaw machine is appropriate) or via a temporary browser session — not
headless.

**Scope**

- Runbook document (Markdown, committed to repo) covering:
  - Pre-flight checklist (Plaid dashboard production access request, app
    review requirements for your institution(s) if applicable)
  - One-time Link flow per institution: how to run it, what the access token
    looks like, where to store it
  - How to populate `items.toml` for the full household structure
  - How to run the first production sync and verify data integrity
  - What to do if an item needs re-linking (lost token, institution forces
    re-auth)
  - Backup and recovery for the SQLite database and config files
- `ledger doctor` extended with a `--production-preflight` flag that checks
  all required config is present before any live credentials are used

**Not in scope**

- Automated Link flow (always manual for OAuth institutions)
- Plaid webhook URL configuration (handled separately via Plaid dashboard)

---

### M8 — systemd deployment

**Goal:** `ledger serve` and `ledger sync` run reliably on the OpenClaw
machine without manual intervention — starting on boot, restarting on
failure, logging to journald, database backed up before it ever holds
production data.

**Scope**

- `ledger serve` as a systemd service unit
  - `Restart=on-failure`, `RestartSec=10`
  - `EnvironmentFile` pointing to the user config `.env`
  - Runs as a dedicated non-root user (or the OpenClaw machine's primary user
    — operator choice, documented either way)
- `ledger sync --all` as a systemd timer
  - Fallback polling in case Plaid webhooks are delayed or missed
  - Suggested interval: every 6 hours (adjustable)
  - Separate timer instances per item are an alternative if `--all` is not
    yet available from M6; documented as a stopgap
- SQLite backup strategy
  - Nightly `sqlite3 .backup` copy to a second path (e.g. an external drive
    or the OpenClaw workspace) via a systemd timer or cron
  - Must run before production data is loaded for the first time
- Log hygiene
  - Existing log format is journald-compatible; confirm and document
  - `CLAW_LOG_LEVEL=INFO` as the production default
- `ledger doctor` as a one-shot systemd service for post-deploy validation
- Installation script (`scripts/install-service.sh`) that writes unit files
  to the correct location for the target user

**Not in scope**

- Docker or container deployment (systemd is the target)
- TLS termination (local-only server, bearer token is the security boundary)
- Remote access / port forwarding

---

### M9 — Hestia SKILL definition

**Goal:** Hestia can query the ledger, annotate transactions, and answer
household finance questions with no manual data preparation. This is the
milestone where the project becomes genuinely useful day-to-day.

**Background**

The `GET /openapi.json` endpoint already provides a machine-readable API
surface. This milestone turns that into a SKILL Hestia loads at startup,
plus the prompting scaffolding that makes her an effective financial
collaborator rather than just an API wrapper.

The annotation table is Hestia's memory. Raw Plaid merchant names (`AMZN
Mktp US*AB12CD`) are noisy; Hestia annotates them once with category, note,
and tags, and every future query benefits from that accumulated context.
Merchant normalization is not a preprocessing problem — it is an emergent
property of Hestia's annotation practice.

**Scope**

- `SKILL.md` for Hestia covering:
  - How to authenticate to the ledger API (bearer token from workspace
    secrets)
  - Query patterns for common household questions (monthly spend by category,
    pending transactions, large transactions above a threshold, spending by
    owner)
  - Annotation workflow: when to annotate, how to choose category/note/tags,
    how to handle recurring merchants consistently
  - Big-purchase decision support flow: query recent spending context, check
    available headroom against known budget targets, summarise trade-offs
  - How to handle the `owner` dimension (shared vs. individual members) in
    queries and summaries
- `AGENTS.md`-style operating constraints for Hestia's ledger interactions:
  - Never attempt to modify `transactions`, `accounts`, or `sync_state` —
    read-only except for `PUT /annotations`
  - Do not surface raw Plaid transaction IDs to the user; use merchant name,
    date, and amount
  - Prefer annotating a transaction once and querying annotations over
    re-interpreting raw data on every session
- Documented example conversations that exercise the core use cases
- `ARCHITECTURE.md` updated to describe Hestia's expected usage patterns

**Not in scope**

- Automated budget rule enforcement (Hestia advises; the human decides)
- Push alerts to messaging channels based on spending thresholds (possible
  future work building on M5 notification infrastructure)
- Multi-user access control (Hestia has full household access by design)

---

## Deferred / Unscheduled

### Markdown export

Human-readable transaction summaries written to the OpenClaw workspace.
Originally planned for M3. Not urgent now that agents have a proper query
API, but may be useful for contexts where tool calls are unavailable or for
manual review workflows. Revisit after M9.

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
---

## Additions / follow-on milestones to capture before production cutover

### M7a — Production Link and item management

**Goal:** Make live Plaid onboarding an implemented operator workflow, not just
a runbook. Production use requires more than documenting the process; the
project needs a clear, repeatable way to create, exchange, store, inspect,
and repair real Plaid items for a multi-owner household.

**Why this exists**

M7 documents the production migration, but the project also needs explicit
implementation work around real Link flows, item status inspection, and
re-link / recovery handling. For this household, expected live items include:

- `first-bank-alice`
- `first-bank-bob`
- `card-co-alice`
- `card-co-bob`

Real institutions may expose overlapping or partially overlapping account
sets across items. The system must be able to model that cleanly without
assuming one institution login maps to one unique household account set.

**Scope**

- Implemented operator workflow for real Plaid Link on a GUI-capable machine
  (OpenClaw box or equivalent)
- Server-side exchange of `public_token` -> `access_token`
- Documented and testable persistence path for:
  - Plaid `item_id`
  - institution name
  - owner tag
  - access token env var name / secret location
  - linked Plaid account IDs returned by that item
- Item inspection command or API surface for:
  - item owner
  - institution
  - last successful sync
  - current health state
  - whether re-auth is required
- Repair / relink workflow for:
  - lost local config
  - `ITEM_LOGIN_REQUIRED`
  - institution-mandated re-auth
  - intentionally replacing an item with a new Link session
- Explicit operator guidance that sandbox and production tokens are fully
  separate and non-portable
- End-to-end production smoke test for linking exactly one real item before
  onboarding the full household

**Not in scope**

- Fully headless Link automation
- Automatic institution-specific troubleshooting beyond surfacing the health
  state and required operator action

---

### M7b — Household identity, overlap, and deduplication

**Goal:** Correctly model a real household where multiple Plaid items may
surface the same underlying financial account and where a shared card may
appear under more than one owner login. Prevent duplicate accounts and
duplicate transactions from polluting the ledger while preserving a clear
audit trail.

**Problem statement**

This household is expected to have overlapping visibility:

- `first-bank` may expose the same underlying bank account under both
  `alice` and `bob` logins
- `card-co` may require both spouses' logins to achieve complete transaction
  visibility, especially if one user's charges are not fully surfaced to the
  other through alerts or account views
- The same real-world account number may therefore appear in multiple Plaid
  items with different owners
- The same real-world transaction may arrive more than once from separate
  items and must not be counted twice

The current M6 owner model is necessary but not sufficient. Owner alone is
not an identity key.

**Design principles to record**

- Distinguish clearly between:
  - **item owner**: who authenticated the Plaid item
  - **account ownership**: who legally/operationally owns the real-world account
  - **account visibility source**: which Plaid items can see that account
- Never assume `(institution, owner)` uniquely identifies a real account
- Never assume account mask alone uniquely identifies a real account
- Preserve raw source records, but expose one canonical account / transaction
  view to operators and Hestia
- Deduplication decisions must be deterministic, explainable, and reversible

**Scope**

- Introduce an explicit account-identity / aliasing design so multiple Plaid
  accounts from different items can be recognized as one canonical household
  account when appropriate
- Add a way to mark account relationships such as:
  - canonical account
  - duplicate view of canonical account
  - uncertain / needs operator review
- Define household account states:
  - `individual`
  - `joint`
  - `shared-liability`
  - `authorized-user-visible`
  - `unknown`
- Record both:
  - item-level owner (`alice`, `bob`, `shared`, etc.)
  - canonical account owner classification (`alice`, `bob`, `joint`,
    `household`, etc.)
- Add transaction dedupe strategy for cross-item overlap, based on a stable
  precedence order and/or matching heuristic using fields such as:
  - posted date / authorized date
  - amount
  - merchant / normalized name
  - pending vs. posted status
  - Plaid `account_id`
  - Plaid transaction ID
  - institution and item source
- Define what counts as the "same transaction" across different items,
  especially for:
  - shared credit-card purchases
  - pending -> posted transitions
  - same charge visible through both users' institution logins
- Add operator-review path for ambiguous duplicates rather than forcing
  silent merges in low-confidence cases
- Persist provenance so a canonical transaction can still answer:
  - which item(s) reported this
  - which raw transaction row won canonical status
  - which raw rows were suppressed as duplicates
- Ensure all spend summaries and future Hestia queries operate on canonical
  transactions, not raw duplicated rows

**Suggested implementation direction**

A reasonable path is to split raw ingestion from canonical household views:

- Raw tables keep exact Plaid payloads per item/account/transaction
- Canonical tables or views resolve:
  - one household account from many source-account aliases
  - one household transaction from many source-transaction candidates

This avoids losing source fidelity while preventing duplicate spend totals.

**Not in scope**

- Perfect automatic dedupe for every institution edge case on day one
- Cross-institution reconciliation of transfers as a full bookkeeping engine
- Double-entry accounting

---

### M7c — Multi-item webhook and automatic sync routing

**Goal:** Webhook-triggered freshness must work for a multi-item household,
not just the single-item environment-variable path.

**Why this exists**

`ledger sync --all` is useful, but production should not depend only on
manual or timer-driven sync. Once four household items are live, webhook
handling must identify the correct item and sync only that item (or safely
fan into the appropriate follow-up action).

**Scope**

- Route Plaid webhooks to the correct configured household item
- Remove single-item assumptions from background sync paths
- Support webhook-triggered sync for all configured items
- Log item ID, institution, and owner on webhook-triggered syncs
- Define fallback behaviour when a webhook references an unknown or stale item
- Preserve idempotency and avoid overlapping duplicate sync runs for the same
  item
- Ensure OpenClaw notifications reflect the correct household item context

**Not in scope**

- Institution-specific webhook customization
- Parallel multi-item fanout unless needed for correctness

---

### M7d — API support for owner and canonical household views

**Goal:** Give Hestia and operator tools a first-class way to reason about
owners, overlapping visibility, and canonical household data without forcing
that logic into prompt folklore.

**Scope**

- `GET /accounts` endpoint exposing:
  - Plaid account ID
  - canonical account ID (if different)
  - institution
  - item ID
  - item owner
  - canonical owner classification
  - dedupe / alias status
- Extend transaction responses or add canonical transaction endpoints so
  clients can distinguish:
  - raw source transaction
  - canonical household transaction
  - duplicate-suppressed source transaction
- Support filtering by:
  - item owner
  - canonical owner
  - institution
  - canonical account
  - raw vs. canonical view
- Document how Hestia should query household-wide totals without double
  counting shared-account overlap

**Not in scope**

- Fine-grained multi-user authorization
- Hiding source provenance from operators

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
