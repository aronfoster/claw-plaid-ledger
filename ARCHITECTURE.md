# Architecture

## Current milestone focus

M11 (advanced agent API & logging) is complete. Sprint 12 adds a spend
summary endpoint, richer transaction filtering, and correlation-ID logging
with secret-safe redaction.

Sprint 12 added:

- `GET /spend` — aggregate spend totals with required date window, optional
  owner filter, AND-semantics tag filters, pending include/exclude control,
  and `view=canonical|raw` support
- `GET /transactions` filter enhancements — optional AND-semantics `tags` and
  `search_notes=true` to include annotation notes in keyword search
- Request correlation middleware — per-request `request_id` appears in log
  lines and response header `X-Request-Id`
- Sync correlation IDs — each sync run carries `sync_run_id`, including CLI
  syncs, scheduled syncs, and webhook-triggered background syncs
- Secret-safe debug logging — webhook payload logging uses redaction; bearer
  tokens, Plaid secrets, and access tokens are never logged

M10 (automation & connectivity) is complete. Sprint 11 adds webhook-first
multi-item routing, an opt-in scheduled sync fallback, and DuckDNS setup
guidance so operators can maintain a stable public webhook URL.

Sprint 11 added:

- Multi-item webhook routing — `POST /webhooks/plaid` now extracts `item_id`
  from the payload, looks up the matching `ItemConfig` in `items.toml`, and
  passes the correct access token to `_background_sync()`.  Unknown item IDs
  log a WARNING and fall back to the `PLAID_ACCESS_TOKEN` single-item path.
- `_background_sync()` refactored — optional `access_token`, `item_id`, and
  `owner` parameters allow per-item context injection; calling with no
  arguments preserves the existing single-item behavior.
- `CLAW_SCHEDULED_SYNC_ENABLED` / `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` —
  opt-in background loop that syncs items silent for longer than the fallback
  window (default 24 h); validated at startup; minimum value is 1.
- FastAPI `lifespan` context manager — starts `_scheduled_sync_loop()` on
  server startup when the scheduled sync is enabled; cancels it cleanly on
  shutdown.
- `_scheduled_sync_loop()` / `_check_and_sync_overdue_items()` /
  `_sync_item_if_overdue()` — helpers that check `sync_state.last_synced_at`
  per item and call `_background_sync()` for overdue items.
- `doctor` scheduled-sync report — new entry reports ENABLED/DISABLED state
  and configuration.
- `scripts/duckdns-update.sh` — POSIX shell script for keeping a DuckDNS
  dynamic DNS record current; suitable for cron or a systemd timer.
- `RUNBOOK.md` sections 10 and 11 — DuckDNS setup walkthrough and scheduled
  sync operations note.

M9 (canonical household views / source precedence) is complete. Sprint 10
adds config-driven suppression metadata (`suppressed_accounts`),
`ledger apply-precedence`, `ledger overlaps`, and a canonical transaction view
that the API now uses by default.

Sprint 10 added:

- `accounts.canonical_account_id` — persisted source-precedence mapping for
  suppressed account rows
- `ledger apply-precedence` — writes configured source-precedence mappings
  into the DB and clears stale suppressions removed from config
- `ledger overlaps` — reports configured suppression status and highlights
  likely unconfigured overlaps
- Canonical query layer in `query_transactions` — default view excludes
  transactions from suppressed accounts while preserving raw records
- `GET /transactions?view=canonical|raw` — canonical-by-default API with
  explicit raw opt-out
- `GET /transactions/{id}` `suppressed_by` field — provenance for source-
  precedence decisions

M8 (multi-item management) is complete. Sprint 9 adds `ledger link` for
browser-based Plaid Link token exchange and `ledger items` for at-a-glance
item health checks across all configured household items.

Sprint 9 added:

- `link_server.py` — local HTTP server for the Plaid Link browser flow
- `ledger link` CLI command — guides the operator through Plaid Link and
  prints the resulting `access_token` and `items.toml` snippet
- `ledger items` CLI command — shows per-item health (token presence,
  account count, last sync timestamp) for all entries in `items.toml`
- `items.toml.example` — committed example file with alice/bob/card-bob
  household structure
- `accounts.item_id` column — associates each account row with the
  originating Plaid item for accurate per-item account counts

M7 (production operations and runbook) is complete. Sprint 8 adds a
committed production runbook and a `ledger doctor --production-preflight`
command that validates live-readiness configuration without contacting any
external service.

Sprint 8 added:

- `preflight.py` — typed, unit-testable production preflight check module
- `ledger doctor --production-preflight` CLI flag that runs preflight
  checks and exits non-zero if any required check fails
- `RUNBOOK.md` — operator runbook covering production prerequisites, cost
  model, access-token lifecycle, sandbox/production isolation, migration
  checklist, backup/recovery, and incident triage

See `RUNBOOK.md` for the step-by-step production onboarding guide.

M6 (multi-institution management) is complete. Sprint 7 adds first-class
household sync support via `items.toml`, `ledger sync --all`, and
`ledger sync --item <id>`. Each configured Plaid item can carry an optional
`owner` tag (for example `alice`, `bob`, or `shared`) that is written to
`sync_state.owner` and `accounts.owner`.

Sprint 7 added:

- `items_config.py` — typed loader for `~/.config/claw-plaid-ledger/items.toml`
- `sync --all` and `sync --item <id>` command paths that resolve per-item
  access tokens from environment-variable names in `items.toml`
- Sequential per-item sync output and partial-failure handling (`--all`
  continues, then exits non-zero if any item failed)
- Owner propagation through sync writes (`sync_state.owner`, `accounts.owner`)
- `doctor` extension: per-item sync-state reporting when `items.toml` is
  present

M5 (OpenClaw notification) is complete. After a webhook-triggered sync that
adds, modifies, or removes at least one transaction, the server sends a `POST`
to OpenClaw's `/hooks/agent` endpoint to wake Hestia as the ingestion worker
by default. Zero-change syncs remain silent. Athena analysis is intentionally
decoupled (scheduled cadence or anomaly-driven). Operators can opt out by
leaving `OPENCLAW_HOOKS_TOKEN` unset — a warning is logged but nothing
crashes.

Sprint 6 added:

- `notifier.py` — sends `POST /hooks/agent` to OpenClaw after a non-empty sync
- Four new configuration variables for the notification endpoint (see Configuration below)
- `doctor` extension: reports `[OK]` or `[WARN]` for OpenClaw notification config

M4 (Agent API and annotation layer) is complete. The server exposes a typed
REST API so OpenClaw agents can query the transaction ledger and write durable
annotations — without ever touching SQLite directly.

Sprint 5 added:

- `GET /transactions` — paginated, filterable transaction list
- `GET /transactions/{transaction_id}` — single transaction with merged annotation
- `PUT /annotations/{transaction_id}` — upsert annotation for a transaction
- `annotations` table — agent-owned annotation storage (sync engine never reads from or writes to it)
- Auto-generated OpenAPI spec at `/openapi.json` and Swagger UI at `/docs`

## Components

- CLI boundary (`typer` library) for operator workflows
- Config/secrets layer (`config.py`)
- SQLite bootstrap and persistence layer (`db.py` + `schema.sql`)
- Multi-item config loader (`items_config.py`) — parses `items.toml` into typed
  item definitions
- Production preflight checks (`preflight.py`) — pure, unit-testable checks
  for live-readiness validation
- Plaid client wrapper (`plaid_adapter.py`)
- Sync engine (`sync_engine.py`)
- HTTP server (`server.py`) — FastAPI application served via uvicorn; includes
  multi-item webhook routing, `_background_sync()` with per-item context
  injection, the `lifespan` context manager, and the optional scheduled sync
  background loop (`_scheduled_sync_loop`, `_check_and_sync_overdue_items`,
  `_sync_item_if_overdue`)
- OpenClaw notifier (`notifier.py`) — sends `POST /hooks/agent` to wake Hestia after a non-empty sync

## Data flow

### Two-agent routing sequence (Sprint 14)

1. **Plaid sync event**: `SYNC_UPDATES_AVAILABLE` arrives and starts a
   background sync for the mapped item.
2. **Hestia annotation pass**: when the sync has non-zero changes, notifier
   wakes Hestia via `/hooks/agent` for ingestion-time annotation updates.
3. **Athena analysis**: Athena runs on its own cadence or when anomalies are
   flagged; it is not woken for every sync event.

```
Plaid API -> sync engine -> SQLite raw records -> canonical view layer -> Agent API -> OpenClaw agent
                  |                                        |
                  +--[non-empty sync]--> OpenClaw /hooks/agent (Hestia wake)
items.toml -------------------------------> source precedence mappings (suppressed_accounts)

items.toml ─┐
            ├─ ledger sync --all ─> [bank-alice] run_sync -> SQLite
            │                    -> [bank-bob]   run_sync -> SQLite
            │                    -> [card-alice] run_sync -> SQLite
PLAID_ENV  ─┘

Webhook-first ingestion (M10):

POST /webhooks/plaid (SYNC_UPDATES_AVAILABLE)
  └─ extract item_id from payload
       ├─ item_id found in items.toml ──> _background_sync(token, item_id, owner)
       ├─ item_id not in items.toml ────> WARNING + _background_sync() [legacy]
       └─ no item_id / no items.toml ──> _background_sync() [legacy]

Scheduled sync fallback (M10, opt-in):

_scheduled_sync_loop (every 60 min)
  └─ _check_and_sync_overdue_items


       ├─ items.toml present ──> per-item last_synced_at check
       │                             overdue → _background_sync(token, item_id, owner)
       │                             recent  → skip (DEBUG log)
       └─ no items.toml ──────> single-item PLAID_ACCESS_TOKEN fallback
```

### Operator handoff (Sprint 14 closeout)

- **Skill install source**: `skills/hestia-ledger/` and `skills/athena-ledger/`
  are copy-ready bundles for downstream agent runtimes.
- **Default wake target**: non-empty sync notifications wake Hestia only.
- **Analysis cadence**: Athena is intentionally decoupled and should run on a
  periodic schedule, optionally prioritizing `needs-athena-review` tags.

The sync engine writes to `transactions`, `accounts`, and `sync_state`. It
never touches `annotations`. Source precedence is applied after sync writes via
`ledger apply-precedence`; canonical filtering happens in query/view logic,
never by deleting raw rows. Agents read transactions and write annotations
exclusively through the HTTP API.

After a webhook-triggered sync where `added + modified + removed > 0`, the
notifier sends a `POST` to the configured OpenClaw `/hooks/agent` endpoint.
Zero-change syncs skip the notification entirely.

## Boundaries

- Secrets stay outside the workspace and are loaded via environment variables.
- SQLite is the source of truth for local financial state.
- Database writes should be deterministic and idempotent across reruns.
- CLI commands orchestrate workflows but should not contain raw Plaid API setup.
- The `annotations` table is entirely agent-owned; the sync engine must never
  read from or write to it.
- Plaid-sourced tables (`transactions`, `accounts`, `sync_state`) are immutable
  from the agent's perspective.

## Key entities

- `account`
- `transaction`
- `annotation` (agent-owned; sync engine never touches this)
- `sync_state`

Deferred entities (`review_item`, rules) land in later phases.

## Schema

### `transactions`

Core Plaid transaction data. Written by the sync engine; read by the API.
Keyed by `plaid_transaction_id` (Plaid-issued stable string, safe for agents
to cache across sessions).

**Effective date:** `COALESCE(posted_date, authorized_date)` — for posted
transactions `posted_date` is set; for pending ones only `authorized_date` is
set.

**Amount sign convention:** positive = money leaving the account
(debit/expense), negative = money entering (credit/income). Amounts are
exposed exactly as stored — not inverted.

### `accounts`

Plaid account metadata. Written by the sync engine on each sync run.
`canonical_account_id` is nullable: `NULL` means the account is canonical;
non-null means the account is suppressed in canonical views and points to the
winning canonical Plaid account.



```sql
CREATE TABLE IF NOT EXISTS accounts (
    id                   INTEGER PRIMARY KEY,
    plaid_account_id     TEXT NOT NULL UNIQUE,
    name                 TEXT NOT NULL,
    mask                 TEXT,
    type                 TEXT NOT NULL,
    subtype              TEXT,
    owner                TEXT,
    item_id              TEXT,
    canonical_account_id TEXT
);
```

### `sync_state`

One row per Plaid item (institution). Stores the Plaid sync cursor and the
timestamp of the last successful sync.

### `annotations`

Agent-owned annotation data. **The sync engine never reads from or writes to
this table.** Created by `init-db`; managed entirely via
`PUT /annotations/{transaction_id}`.

```sql
CREATE TABLE IF NOT EXISTS annotations (
    id                   INTEGER PRIMARY KEY,
    plaid_transaction_id TEXT NOT NULL UNIQUE
                         REFERENCES transactions(plaid_transaction_id),
    category             TEXT,
    note                 TEXT,
    tags                 TEXT,          -- JSON array stored as text, e.g. '["food","recurring"]'
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
```

Column notes:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Auto-incrementing primary key |
| `plaid_transaction_id` | TEXT | FK to `transactions.plaid_transaction_id`; unique per annotation |
| `category` | TEXT \| NULL | Agent-assigned category label |
| `note` | TEXT \| NULL | Free-text agent note |
| `tags` | TEXT \| NULL | JSON array stored as text (e.g. `'["food","recurring"]'`); `null` when no tags |
| `created_at` | TEXT | ISO 8601 UTC timestamp; set on first insert; never changed on update |
| `updated_at` | TEXT | ISO 8601 UTC timestamp; updated on every upsert |

## Interfaces

Current operator-facing CLI commands:

- `doctor` — validates config, DB connectivity, schema, and reports row counts;
  with `--verbose` shows redacted config values
- `apply-precedence` — reads `suppressed_accounts` from `items.toml` and
  writes source-precedence mappings to `accounts.canonical_account_id`;
  clears stale mappings no longer present in config
- `doctor --production-preflight` — validates live-readiness configuration
  without contacting external services; exits non-zero if any required check
  fails; see `RUNBOOK.md` for usage in the production onboarding checklist
- `init-db` — creates the SQLite database and initializes the schema (safe to
  run against an existing database; uses `CREATE TABLE IF NOT EXISTS`)
- `items` — shows per-item health (token presence, account count, last sync
  timestamp) for all entries in `items.toml`; exits 0 always; the standard
  daily health-check command before running `sync --all`
- `link` — guides the operator through the Plaid Link browser flow and prints
  the resulting `access_token` and `items.toml` snippet
- `overlaps` — displays configured suppression rules, DB status
  (`IN DB`/`MISMATCH`/`NOT YET SYNCED`), and potential unconfirmed overlaps
- `sync` — fetches transactions from Plaid and persists them to SQLite;
  `sync --all` is the standard household ingestion path; `sync --item <id>`
  syncs a single item from `items.toml`
- `serve` — starts the FastAPI/uvicorn HTTP server; binds to
  `CLAW_SERVER_HOST:CLAW_SERVER_PORT` (default `127.0.0.1:8000`)

## HTTP endpoints

All endpoints are served by `ledger serve`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Service liveness check; returns `{"status": "ok"}` |
| `POST` | `/webhooks/plaid` | Bearer | Receives Plaid webhook events; triggers background sync on `SYNC_UPDATES_AVAILABLE` |
| `GET` | `/transactions` | Bearer | Paginated, filtered transaction list (supports tags and optional note search) |
| `GET` | `/spend` | Bearer | Aggregate spend total and count for a date window with optional owner/tag filters |
| `GET` | `/transactions/{transaction_id}` | Bearer | Single transaction with merged annotation and suppression provenance |
| `PUT` | `/annotations/{transaction_id}` | Bearer | Upsert annotation for a transaction |
| `GET` | `/openapi.json` | None | Auto-generated OpenAPI spec (FastAPI); no authentication required |
| `GET` | `/docs` | None | Swagger UI (FastAPI); local use only; no authentication required |

### `GET /health`

Returns `{"status": "ok"}`. No authentication required.

### `POST /webhooks/plaid`

Receives Plaid webhook events. Requires bearer token auth and Plaid
HMAC-SHA256 signature verification (`Plaid-Verification` header). Returns 400
on invalid signature.

#### Webhook ingress IP allowlisting

When `CLAW_WEBHOOK_ALLOWED_IPS` is configured, a middleware layer enforces
source IP filtering before any route handler runs.  IP resolution order:

1. If the direct connection IP is in `CLAW_TRUSTED_PROXIES` (default:
   `127.0.0.1`), take the **leftmost** `X-Forwarded-For` address as the real
   client IP.
2. Otherwise, use the direct connection IP.

If the resolved IP does not fall within any configured CIDR, the middleware
returns HTTP 403 `{"detail": "forbidden"}` and logs a WARNING with the
resolved IP and `request_id`.  The route handler and all other middleware
downstream are bypassed.

When `CLAW_WEBHOOK_ALLOWED_IPS` is unset or empty, the middleware is
transparent and all other routes are unaffected in all configurations.

On `SYNC_UPDATES_AVAILABLE`:

1. Extracts `item_id` from the payload.
2. If `item_id` is present and matches a configured `ItemConfig` in
   `items.toml`, enqueues `_background_sync()` with that item's access token,
   item ID, and owner.
3. If `item_id` is present but not found in `items.toml`, logs a WARNING and
   falls back to the `PLAID_ACCESS_TOKEN` single-item sync.
4. If `item_id` is absent or `items.toml` is not loadable, falls back to the
   single-item sync silently.

Returns 200 immediately; the sync runs in the background.
Unrecognised webhook types are acknowledged with 200 and logged at warning
level.

### `GET /transactions`

Returns a paginated, filtered list of transactions.

**Query parameters** (all optional):

| Parameter | Type | Default | Description |
|---|---|---|---|
| `start_date` | `YYYY-MM-DD` | — | Filter: effective date ≥ start_date (inclusive). Effective date = `COALESCE(posted_date, authorized_date)` |
| `end_date` | `YYYY-MM-DD` | — | Filter: effective date ≤ end_date (inclusive) |
| `account_id` | string | — | Filter: exact match on `plaid_account_id` |
| `pending` | bool | — | Filter: `true` returns only pending; `false` returns only posted |
| `min_amount` | float | — | Filter: amount ≥ min_amount (inclusive). Plaid sign: positive = debit |
| `max_amount` | float | — | Filter: amount ≤ max_amount (inclusive) |
| `keyword` | string | — | Filter: case-insensitive substring match on `name` and `merchant_name`; also matches `annotations.note` when `search_notes=true` |
| `tags` | list[string] | `[]` | Filter: transaction annotation must include **all** listed tags (AND semantics); pass `?tags=a&tags=b` |
| `search_notes` | bool | `false` | If true and `keyword` is set, include annotation `note` in keyword search |
| `limit` | int | `100` | Maximum rows to return; max `500`; `limit > 500` returns HTTP 422 |
| `offset` | int | `0` | Number of matching rows to skip (for pagination) |
| `view` | `canonical` \\| `raw` | `canonical` | `canonical` excludes suppressed-account rows via source precedence; `raw` returns all rows |

**Response** (HTTP 200):

```json
{
  "transactions": [
    {
      "id": "<plaid_transaction_id>",
      "account_id": "<plaid_account_id>",
      "amount": 12.34,
      "iso_currency_code": "USD",
      "name": "Starbucks",
      "merchant_name": "Starbucks",
      "pending": false,
      "date": "2024-01-15"
    }
  ],
  "total": 150,
  "limit": 100,
  "offset": 0
}
```

- `total` is the full matching count before `limit`/`offset` are applied.
- Empty result set returns HTTP 200 with `"transactions": []` and `"total": 0`.
- `date` is `COALESCE(posted_date, authorized_date)`.

### `GET /spend`

Returns aggregate spend totals for a required date window.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `start_date` | `YYYY-MM-DD` | required | Window start (inclusive). Missing or invalid values return HTTP 422. |
| `end_date` | `YYYY-MM-DD` | required | Window end (inclusive). Missing or invalid values return HTTP 422. |
| `owner` | string | — | Restrict to accounts tagged with this owner (`accounts.owner`). |
| `tags` | list[string] | `[]` | Annotation tags filter with AND semantics (`?tags=a&tags=b`). |
| `include_pending` | bool | `false` | Include pending transactions when true; otherwise only posted rows are summed. |
| `view` | `canonical` \| `raw` | `canonical` | Canonical excludes suppressed-account rows; raw includes all rows. |

**Response** (HTTP 200):

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

- `total_spend` is the arithmetic sum of `amount` (Plaid sign convention is preserved).
- `transaction_count` is the number of matching rows before aggregation.
- Empty windows return zeros (`total_spend=0`, `transaction_count=0`) not `null`.

### `GET /transactions/{transaction_id}`

Returns full detail for one transaction, including a merged annotation block.
`transaction_id` in the path is the `plaid_transaction_id` string.

Returns HTTP 404 if not found.

**Response** (HTTP 200):

```json
{
  "id": "<plaid_transaction_id>",
  "account_id": "<plaid_account_id>",
  "amount": 12.34,
  "iso_currency_code": "USD",
  "name": "Starbucks",
  "merchant_name": "Starbucks",
  "pending": false,
  "date": "2024-01-15",
  "raw_json": "{...}",
  "annotation": {
    "category": "food",
    "note": "Morning coffee",
    "tags": ["discretionary", "recurring"],
    "updated_at": "2024-01-16T10:30:00Z"
  }
}
```

- `annotation` is `null` if no annotation exists for this transaction.
- `tags` in the response is a parsed JSON list (not the raw text stored in
  SQLite); if stored value is `null`, returns `null` for tags.
- `raw_json` is the raw Plaid API payload stored at sync time; may be `null`
  for transactions synced before this field was populated.

### `PUT /annotations/{transaction_id}`

Creates or fully replaces an annotation for a transaction.
`transaction_id` in the path is the `plaid_transaction_id` string.

This is a **full replace**, not a partial PATCH: every PUT completely overwrites
the annotation row. Omitted fields are stored as `null`.

**Request body** (all fields optional):

```json
{
  "category": "food",
  "note": "Morning coffee",
  "tags": ["discretionary", "recurring"]
}
```

- `tags` must be a JSON array of strings or `null`.
- Returns HTTP 404 if `transaction_id` does not exist in `transactions`.
  Agents cannot annotate phantom transactions.
- Returns HTTP 200 `{"status": "ok"}` on successful create or update.
- `created_at` is preserved on updates; `updated_at` is refreshed.

## OpenAPI / SKILL definition

FastAPI auto-generates a machine-readable OpenAPI spec at `GET /openapi.json`
and a Swagger UI at `GET /docs`. Both are served without authentication
(consistent with the local-only security posture).

`GET /openapi.json` is the **canonical machine-readable spec** and is intended
to seed the OpenClaw SKILL definition (M11). Any agent that needs to
introspect the available API surface should fetch this endpoint rather than
reading the source code.

## OpenClaw notification

After a webhook-triggered sync, `_background_sync` in `server.py` calls
`notify_openclaw` from `notifier.py` when `summary.added + summary.modified +
summary.removed > 0`.

### When notification fires

- A Plaid `SYNC_UPDATES_AVAILABLE` webhook triggers a background sync.
- The sync returns a non-zero change count (at least one added, modified, or
  removed transaction).
- `OPENCLAW_HOOKS_TOKEN` is set to a non-empty value.

### When notification is skipped

- **Zero-change syncs:** if `added + modified + removed == 0`, the notifier is
  not called. The existing `logger.warning("sync returned no changes")` line
  fires instead.
- **Token not set:** if `OPENCLAW_HOOKS_TOKEN` is absent or set to an empty
  string, `notify_openclaw` logs a `WARNING` (`"OPENCLAW_HOOKS_TOKEN not set —
  skipping notification"`) and returns immediately. This is not an error; it is
  a valid operator choice.

### Failure behaviour

Network errors (`urllib.error.URLError`) and non-2xx HTTP responses
(`urllib.error.HTTPError`) are caught inside `notify_openclaw`, logged at
`WARNING`, and never re-raised. The background sync task always completes
normally regardless of notification outcome.

### Payload shape

```json
{
  "message": "Plaid sync complete: 3 added, 1 modified. Hestia should run ingestion annotations; Athena reviews later on schedule or anomaly flags.",
  "name": "Hestia",
  "wakeMode": "now"
}
```

| Field | Description |
|---|---|
| `message` | Human-readable summary of non-zero change counts plus explicit Hestia-first / Athena-later routing guidance |
| `name` | Name of the OpenClaw agent to wake; controlled by `OPENCLAW_HOOKS_AGENT` |
| `wakeMode` | Wake mode for OpenClaw; controlled by `OPENCLAW_HOOKS_WAKE_MODE` (`now` is the only supported value) |

The message is built by joining the non-zero count fragments
(`"N added"`, `"N modified"`, `"N removed"`) with `", "` and appending
`". Hestia should run ingestion annotations; Athena reviews later on
schedule or anomaly flags."`.

### HTTP request

The notifier uses `urllib.request` (Python standard library) — no new runtime
dependency is added. `httpx` remains a dev/test-only dependency.

```
POST <OPENCLAW_HOOKS_URL>
Content-Type: application/json
Authorization: Bearer <OPENCLAW_HOOKS_TOKEN>
```


## Multi-institution management

### Purpose

M6 introduces multi-item sync so one command can process every Plaid item in a
household (for example personal bank, shared credit card, partner accounts)
without manually editing environment variables between runs.

### `items.toml` location and format

Default path:

`~/.config/claw-plaid-ledger/items.toml`

Example (see also `items.toml.example` at the repo root):

```toml
[[items]]
id                = "bank-alice"
access_token_env  = "PLAID_ACCESS_TOKEN_BANK_ALICE"
owner             = "alice"

[[items]]
id                = "card-alice"
access_token_env  = "PLAID_ACCESS_TOKEN_CARD_ALICE"
owner             = "alice"

[[items]]
id                = "card-bob"
access_token_env  = "PLAID_ACCESS_TOKEN_CARD_BOB"
owner             = "bob"
```

Fields:

| Field | Required | Type | Description |
|---|---|---|---|
| `id` | yes | string | Operator-assigned item identifier used as `sync_state.item_id` |
| `access_token_env` | yes | string | Name of the environment variable that stores this item's Plaid access token |
| `owner` | no | string \| null | Free-form ownership tag; omitted/null is treated as `None` |

### `sync --all` behavior

- The CLI loads all `ItemConfig` entries from `items.toml`.
- It constructs one shared Plaid adapter from shared client credentials
  (`PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`).
- It iterates items sequentially and resolves each access token from the
  configured `access_token_env` name at runtime.
- Each item is synced independently via `run_sync` with its own `item_id`,
  cursor, and `owner`.

Per-item failures are isolated: if one item fails (missing env var, invalid
token, network issue), the run logs an error for that item and continues with
the remaining items. The command exits with status code 1 if any item failed,
or 0 if all items succeeded.

### Owner semantics and design decision

The `owner` tag is stored on:

- `sync_state.owner`
- `accounts.owner`

Hestia uses this metadata by first determining which accounts belong to which
item, then filtering transaction queries by `account_id`. No `owner` column was
added to `transactions`, and no Agent API contract changes were required.

Design decision: ownership scoping is a naming convention anchored at the
item/account level, not a transaction-level schema dimension. This keeps
transaction storage unchanged and avoids duplicating owner metadata across every
transaction row.

### Legacy single-item mode remains valid

The legacy path (`ledger sync` with no item flags) still reads:

- `PLAID_ACCESS_TOKEN`
- `CLAW_PLAID_LEDGER_ITEM_ID` (default `default-item`)

This mode behaves as before and writes `owner=None`.

## Configuration

All configuration is loaded from environment variables (or a user `.env` file
at `~/.config/claw-plaid-ledger/.env`). See `.env.example` for all supported
keys.

Key variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLAW_PLAID_LEDGER_DB_PATH` | yes | — | Path to the SQLite database file |
| `PLAID_CLIENT_ID` | for sync | — | Plaid API client ID |
| `PLAID_SECRET` | for sync | — | Plaid API secret |
| `PLAID_ENV` | for sync | — | Plaid environment (`sandbox`, `production`) |
| `PLAID_ACCESS_TOKEN` | for sync (single-item mode) | — | Plaid access token for the linked item |
| `CLAW_PLAID_LEDGER_ITEM_ID` | no | `default-item` | Item ID for single-item mode (legacy path) |
| `CLAW_PLAID_LEDGER_WORKSPACE_PATH` | no | — | Path to OpenClaw workspace for exports |
| `CLAW_SERVER_HOST` | no | `127.0.0.1` | Host for `ledger serve` to bind to (local-only by default) |
| `CLAW_SERVER_PORT` | no | `8000` | TCP port for `ledger serve` to listen on |
| `CLAW_API_SECRET` | for serve | — | Bearer token required on all non-health HTTP endpoints; server refuses to start if unset |
| `PLAID_WEBHOOK_SECRET` | for webhooks | — | Shared secret used to verify Plaid webhook HMAC-SHA256 signatures; if unset all webhook signature checks fail closed |
| `CLAW_LOG_LEVEL` | no | `INFO` | Log level for the HTTP server; must be one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; invalid value raises `ConfigError` at startup |
| `OPENCLAW_HOOKS_URL` | no | `http://127.0.0.1:18789/hooks/agent` | OpenClaw `/hooks/agent` endpoint URL |
| `OPENCLAW_HOOKS_TOKEN` | no | — | Bearer token for OpenClaw; if unset, notification is skipped with a warning |
| `OPENCLAW_HOOKS_AGENT` | no | `Hestia` | Name of the OpenClaw ingestion agent to wake (Hestia in the two-agent flow) |
| `OPENCLAW_HOOKS_WAKE_MODE` | no | `now` | Wake mode passed to OpenClaw (`now` is the only supported value) |
| `CLAW_SCHEDULED_SYNC_ENABLED` | no | `false` | Enable the scheduled sync fallback loop; set to `true` to activate |
| `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` | no | `24` | Hours of sync silence before an item is treated as overdue; minimum 1; values ≤ 0 cause a startup error |
| `CLAW_WEBHOOK_ALLOWED_IPS` | no | — | Comma-separated IPv4/IPv6 CIDRs allowed to POST to `/webhooks/plaid`; unset = no IP filtering |
| `CLAW_TRUSTED_PROXIES` | no | `127.0.0.1` | Comma-separated IPs of trusted reverse proxies for `X-Forwarded-For` resolution; used only when `CLAW_WEBHOOK_ALLOWED_IPS` is set |

`items.toml` is a separate configuration file (not an environment variable)
used by `sync --all` and `sync --item`. Default path:
`~/.config/claw-plaid-ledger/items.toml`.

## Runtime and tooling standards

- Python: 3.12+
- Environment/dependency management: `uv`
- CLI framework: `typer` (real library, not a shim)
- HTTP framework: `fastapi` + `uvicorn[standard]`
- Datastore: standard-library `sqlite3`
- Testing: `pytest` + `fastapi.testclient.TestClient`
- Formatting/linting: `ruff format` + `ruff check`
- Type-checking: `mypy --strict`

## Repository layout (current)

```text
src/claw_plaid_ledger/
  __init__.py
  cli.py
  config.py
  db.py
  items_config.py   # multi-item items.toml loader
  link_server.py    # local HTTP server for Plaid Link flow (M8)
  notifier.py
  plaid_adapter.py
  plaid_models.py
  preflight.py      # production preflight check logic (M7)
  schema.sql
  server.py         # webhook routing, scheduled sync loop (M10)
  sync_engine.py
  webhook_auth.py

scripts/
  duckdns-update.sh # DuckDNS IP-update script for cron/systemd (M10)
  install-hooks.sh

tests/
  test_cli.py
  test_config.py
  test_db.py
  test_items_config.py
  test_link_server.py
  test_notifier.py
  test_plaid_adapter.py
  test_preflight.py
  test_server.py
  test_sync_engine.py
  test_webhook_auth.py

items.toml.example  # household configuration example (M8)
pyproject.toml
README.md
AGENTS.md
ARCHITECTURE.md
BUGS.md
ROADMAP.md
RUNBOOK.md          # production operations runbook (M7+, M10)
SPRINT.md
VISION.md
```

## Quality gate

A change is ready to merge only when all required checks pass:

1. `uv run --locked ruff format . --check`
2. `uv run --locked ruff check .`
3. `uv run --locked mypy .`
4. `uv run --locked pytest`

## Logging conventions

The runtime uses a correlation-aware log format:

```
%(asctime)s %(levelname)s %(name)s [%(correlation_id)s]: %(message)s
```

Correlation behavior:

- Request scope: middleware generates `request_id` (`req-xxxxxxxx`), stores it
  in request context, logs request start/end, and returns `X-Request-Id` on
  every response.
- Sync scope: each sync run emits a `sync_run_id` (`sync-xxxxxxxx`) propagated
  through sync-layer log lines (CLI, scheduled loop, webhook-triggered sync).
- Outside request/sync scope, `correlation_id` renders as `-`.

Secret-redaction policy:

- Never log bearer tokens, Plaid secrets, or Plaid access tokens at any level.
- DEBUG webhook payload logs must be redacted first (remove token/secret/password
  fields and sensitive headers) before emission.
- Transaction/account data and sync cursors may be logged at DEBUG/INFO as needed
  for operations.
