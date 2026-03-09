# Roadmap

## M0 - Project skeleton

Repo structure, config, logging, CLI entrypoints, docs.

### Status

Complete. Python + uv baseline, strict quality tooling, environment-backed
configuration, and SQLite bootstrap are in place.

## M1 - Plaid connection and initial sync

Add Plaid client integration and implement the first transaction sync path into
SQLite with cursor-based idempotent reruns.

### Status

Complete.

## M2 - Local ledger hardening

Expand deterministic persistence behavior for accounts, transactions, and sync
state; improve operational diagnostics.

### Status

Complete. BUG-001 (account_count inflation) and BUG-002 (Typer shim) resolved.
Real `typer` library adopted. `doctor` command hardened with real diagnostics.
`CLAW_PLAID_LEDGER_ITEM_ID` is configurable for multi-institution households.
Sync loop handles mid-loop exceptions without corrupting cursor state.

## M3 - Server skeleton and webhook receiver

Stand up the HTTP server and wire Plaid webhooks to the sync engine. This
milestone is purely about the **inbound** side: Plaid can reach the server,
webhooks trigger syncs, and the server is secured from day one.

### Plaid billing note

`/transactions/sync` is subscription-billed per connected account per month —
calling it in response to webhooks does not incur per-call charges. The
billable per-request endpoint is `/transactions/refresh` (on-demand force
update); this should never be called in tests or automation. The normal
webhook-driven sync flow is safe to invoke freely.

On first Item activation, Plaid fires `SYNC_UPDATES_AVAILABLE` with
`initial_update_complete: true` when recent data is ready, then again with
`historical_update_complete: true` when full history is available. The cursor
drains naturally across both events — no special first-run flag is needed.

### Scope

- Add FastAPI as a dependency
- Add `ledger serve` CLI command to start the HTTP server
- `GET /health` — unauthenticated liveness check
- `POST /webhooks/plaid` — receive Plaid `SYNC_UPDATES_AVAILABLE` webhooks
  and enqueue a sync against the existing sync engine; respond within 10
  seconds (Plaid's timeout) by queuing, not blocking
- Static bearer token auth on all non-health endpoints
  (`CLAW_API_SECRET` in `.env`); server refuses to start if unset
- Webhook signature verification (Plaid HMAC) — include from the start
  given Plaid's production security requirements
- `doctor` command extended to verify `CLAW_API_SECRET` is set

### Not in scope

- Agent query/annotation API (M4)
- OpenClaw notification (M5)
- Any markdown export

## M4 - Agent API and annotation layer

Expose a typed REST API for OpenClaw agents to query transactions and write
structured annotations. This is the **outbound** side: agents can read the
ledger and leave durable notes without ever touching SQLite directly.

### Scope

- `annotations` table added to schema — separate from Plaid-sourced data,
  which remains immutable from the agent's perspective; agents own this
  table entirely, sync engine never touches it
- `GET /transactions` — query with filters (date range, account, pending,
  amount range, keyword); paginated
- `GET /transactions/{id}` — single transaction detail including its
  annotation if present
- `PUT /annotations/{transaction_id}` — upsert agent-authored annotation
  (category, note, tags as JSON array); idempotent
- All agent endpoints require bearer token
- OpenAPI spec generated automatically; this becomes the source of truth
  for the OpenClaw SKILL definition

### Security posture

Physical machine and OS are the primary security boundary. The bearer token
provides a meaningful second layer and establishes the correct habit of never
exposing an unauthenticated financial endpoint, even locally. Per-agent token
scoping is deferred unless a concrete threat model requires it.

## M5 - Change-triggered notification

After a webhook-triggered sync, wake OpenClaw when there are new or modified
transactions that warrant review. The exact mechanism (HTTP callback, file
signal, or direct agent invocation) depends on OpenClaw's architecture at the
time.

## M6 - Basic intelligence

Rules for merchant normalization, category hints, pending/posting
reconciliation. Likely server-side logic that enriches records before the
agent API serves them, reducing annotation burden on agents.

## M7 - OSS hardening

Install docs, sample config, packaging, security notes, example OpenClaw
SKILL definition generated from the OpenAPI spec.

## Future / unscheduled

### Markdown export

Generating human-readable transaction summaries into the OpenClaw workspace
was the original M3 plan. It is not urgent now that agents have a proper query
API, but may still be useful for human review or for OpenClaw contexts where
tool calls are unavailable. Revisit after M5.

### Multi-institution sync UX

`CLAW_PLAID_LEDGER_ITEM_ID` is a single string set per invocation. A
household with more than one institution must run `ledger sync` multiple
times. First-class multi-institution support (TOML config, `ledger sync
--all`) is deferred until the single-institution path is stable in server
mode.

### Per-agent token scoping

If multiple OpenClaw agents with different trust levels need access,
replace the single `CLAW_API_SECRET` with a small token table mapping
tokens to permission scopes (read-only vs. read-write annotations).
