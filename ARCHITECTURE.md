# Architecture

## Current milestone focus

M5 (OpenClaw notification) is complete. After a webhook-triggered sync that
adds, modifies, or removes at least one transaction, the server sends a `POST`
to OpenClaw's `/hooks/agent` endpoint to wake the configured agent (Hestia by
default). Zero-change syncs remain silent. Operators can opt out by leaving
`OPENCLAW_HOOKS_TOKEN` unset — a warning is logged but nothing crashes.

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
- Plaid client wrapper (`plaid_adapter.py`)
- Sync engine (`sync_engine.py`)
- HTTP server (`server.py`) — FastAPI application served via uvicorn
- OpenClaw notifier (`notifier.py`) — sends `POST /hooks/agent` to wake Hestia after a non-empty sync

## Data flow

```
Plaid API -> sync engine -> SQLite -> Agent API -> OpenClaw agent
                  |
                  +--[non-empty sync]--> OpenClaw /hooks/agent (Hestia wake)
```

The sync engine writes to `transactions`, `accounts`, and `sync_state`. It
never touches `annotations`. Agents read transactions and write annotations
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
- `init-db` — creates the SQLite database and initializes the schema (safe to
  run against an existing database; uses `CREATE TABLE IF NOT EXISTS`)
- `sync` — fetches transactions from Plaid and persists them to SQLite;
  respects `CLAW_PLAID_LEDGER_ITEM_ID` for multi-institution households
- `serve` — starts the FastAPI/uvicorn HTTP server; binds to
  `CLAW_SERVER_HOST:CLAW_SERVER_PORT` (default `127.0.0.1:8000`)

## HTTP endpoints

All endpoints are served by `ledger serve`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Service liveness check; returns `{"status": "ok"}` |
| `POST` | `/webhooks/plaid` | Bearer | Receives Plaid webhook events; triggers background sync on `SYNC_UPDATES_AVAILABLE` |
| `GET` | `/transactions` | Bearer | Paginated, filtered transaction list |
| `GET` | `/transactions/{transaction_id}` | Bearer | Single transaction with merged annotation |
| `PUT` | `/annotations/{transaction_id}` | Bearer | Upsert annotation for a transaction |
| `GET` | `/openapi.json` | None | Auto-generated OpenAPI spec (FastAPI); no authentication required |
| `GET` | `/docs` | None | Swagger UI (FastAPI); local use only; no authentication required |

### `GET /health`

Returns `{"status": "ok"}`. No authentication required.

### `POST /webhooks/plaid`

Receives Plaid webhook events. Requires bearer token auth and Plaid
HMAC-SHA256 signature verification (`Plaid-Verification` header). Returns 400
on invalid signature. On `SYNC_UPDATES_AVAILABLE` enqueues a background sync
via `run_sync` and returns 200 immediately. Unrecognised webhook types are
acknowledged with 200 and logged at debug level.

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
| `keyword` | string | — | Filter: case-insensitive substring match on `name` and `merchant_name` |
| `limit` | int | `100` | Maximum rows to return; max `500`; `limit > 500` returns HTTP 422 |
| `offset` | int | `0` | Number of matching rows to skip (for pagination) |

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
to seed the OpenClaw SKILL definition for M7. Any agent that needs to
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
  "message": "Plaid sync complete: 3 added, 1 modified. Review new transactions and annotate as appropriate.",
  "name": "Hestia",
  "wakeMode": "now"
}
```

| Field | Description |
|---|---|
| `message` | Human-readable summary of non-zero change counts plus a review prompt |
| `name` | Name of the OpenClaw agent to wake; controlled by `OPENCLAW_HOOKS_AGENT` |
| `wakeMode` | Wake mode for OpenClaw; controlled by `OPENCLAW_HOOKS_WAKE_MODE` (`now` is the only supported value) |

The message is built by joining the non-zero count fragments
(`"N added"`, `"N modified"`, `"N removed"`) with `", "` and appending
`". Review new transactions and annotate as appropriate."`.

### HTTP request

The notifier uses `urllib.request` (Python standard library) — no new runtime
dependency is added. `httpx` remains a dev/test-only dependency.

```
POST <OPENCLAW_HOOKS_URL>
Content-Type: application/json
Authorization: Bearer <OPENCLAW_HOOKS_TOKEN>
```

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
| `PLAID_ACCESS_TOKEN` | for sync | — | Plaid access token for the linked item |
| `CLAW_PLAID_LEDGER_ITEM_ID` | no | `default-item` | Sync-state key; one value per institution |
| `CLAW_PLAID_LEDGER_WORKSPACE_PATH` | no | — | Path to OpenClaw workspace for exports |
| `CLAW_SERVER_HOST` | no | `127.0.0.1` | Host for `ledger serve` to bind to (local-only by default) |
| `CLAW_SERVER_PORT` | no | `8000` | TCP port for `ledger serve` to listen on |
| `CLAW_API_SECRET` | for serve | — | Bearer token required on all non-health HTTP endpoints; server refuses to start if unset |
| `PLAID_WEBHOOK_SECRET` | for webhooks | — | Shared secret used to verify Plaid webhook HMAC-SHA256 signatures; if unset all webhook signature checks fail closed |
| `CLAW_LOG_LEVEL` | no | `INFO` | Log level for the HTTP server; must be one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; invalid value raises `ConfigError` at startup |
| `OPENCLAW_HOOKS_URL` | no | `http://127.0.0.1:18789/hooks/agent` | OpenClaw `/hooks/agent` endpoint URL |
| `OPENCLAW_HOOKS_TOKEN` | no | — | Bearer token for OpenClaw; if unset, notification is skipped with a warning |
| `OPENCLAW_HOOKS_AGENT` | no | `Hestia` | Name of the OpenClaw agent to wake |
| `OPENCLAW_HOOKS_WAKE_MODE` | no | `now` | Wake mode passed to OpenClaw (`now` is the only supported value) |

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
  notifier.py
  plaid_adapter.py
  plaid_models.py
  schema.sql
  server.py
  sync_engine.py
  webhook_auth.py

tests/
  test_cli.py
  test_config.py
  test_db.py
  test_notifier.py
  test_plaid_adapter.py
  test_server.py
  test_sync_engine.py
  test_webhook_auth.py

pyproject.toml
README.md
AGENTS.md
ARCHITECTURE.md
BUGS.md
ROADMAP.md
SPRINT.md
VISION.md
```

## Quality gate

A change is ready to merge only when all required checks pass:

1. `uv run --locked ruff format . --check`
2. `uv run --locked ruff check .`
3. `uv run --locked mypy .`
4. `uv run --locked pytest`
