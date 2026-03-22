# claw-plaid-ledger

Local-first financial management support app that ingests Plaid data into SQLite,
exposes a typed REST API for OpenClaw agents, and wakes agents after non-empty syncs.
Bring-your-own-Plaid-integration. You're responsible for safeguarding the data at rest
and keeping OpenClaw interactions safe.

## Tech stack

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment, dependency, and task flow
- [Typer](https://typer.tiangolo.com/) for CLI UX
- [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) for the HTTP API server
- `sqlite3` as the local source-of-truth datastore
- [pytest](https://docs.pytest.org/) for tests
- [Ruff](https://docs.astral.sh/ruff/) for linting and formatting
- [mypy](https://mypy.readthedocs.io/) for strict type checking

## Quick start

```bash
uv run ledger --help
uv run --locked pytest
uv run --locked ruff format . --check
uv run --locked ruff check .
uv run --locked mypy .
```

## Two-agent skill bundle quickstart

Install the split skill bundles into your Codex/OpenClaw skills directory:

```bash
rm -rf ~/.openclaw/workspace/agents/hestia/skills/hestia-ledger ~/.openclaw/workspace/agents/athena/skills/athena-ledger
mkdir -p ~/.openclaw/workspace/agents/hestia/skills ~/.openclaw/workspace/agents/athena/skills
cp -R skills/hestia-ledger ~/.openclaw/workspace/agents/hestia/skills/
cp -R skills/athena-ledger ~/.openclaw/workspace/agents/athena/skills/
```

Recommended operating cadence:

- **Hestia (bookkeeper)**: event-driven; wake on non-empty Plaid webhook syncs.
- **Athena (analyst)**: periodic (daily/weekly) plus targeted review of
  `needs-athena-review` tagged transactions.

## Local configuration

Claw Plaid Ledger expects secrets and machine-specific paths to live outside
this repository.

Recommended location on Linux:

```bash
~/.config/claw-plaid-ledger/.env
```

This keeps secrets out of git and out of the OpenClaw workspace.

Create the config directory and install the template:

```bash
mkdir -p ~/.config/claw-plaid-ledger
chmod 700 ~/.config/claw-plaid-ledger
cp .env.example ~/.config/claw-plaid-ledger/.env
chmod 600 ~/.config/claw-plaid-ledger/.env
```

Then edit:

```bash
~/.config/claw-plaid-ledger/.env
```

with your Plaid credentials and local paths.

The app loads configuration from both places:

1. `~/.config/claw-plaid-ledger/.env` (if it exists)
2. Runtime environment variables

Runtime environment variables override values from the user env file.

## Security model

Keep these boundaries:

- **Repository**: source code only
- **User config**: secrets and machine-specific settings
- **Database**: local ledger state in SQLite
- **OpenClaw workspace**: agent-readable exports only

Never store Plaid secrets:

- in the git repository
- in committed files
- in markdown files
- in the OpenClaw workspace

## Getting an Access Token

Use the built-in browser flow — it handles the public-token creation and
exchange automatically and prints a ready-to-paste `items.toml` snippet:

```bash
uv run ledger link
```

See `RUNBOOK.md` Section 2.3 for the full walkthrough.

## Configuration reference

The template file `.env.example` includes all supported keys. Key variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLAW_PLAID_LEDGER_DB_PATH` | yes | — | Path to the SQLite database file |
| `PLAID_CLIENT_ID` | for sync | — | Plaid API client ID |
| `PLAID_SECRET` | for sync | — | Plaid API secret |
| `PLAID_ENV` | for sync | `sandbox` | Plaid environment (`sandbox` or `production`) |
| `PLAID_ACCESS_TOKEN` | for sync | — | Plaid access token for the linked item |
| `CLAW_PLAID_LEDGER_ITEM_ID` | no | `default-item` | Sync-state key; one value per institution |
| `CLAW_PLAID_LEDGER_WORKSPACE_PATH` | no | — | Path to OpenClaw workspace for exports |
| `CLAW_SERVER_HOST` | no | `127.0.0.1` | Host for `ledger serve` to bind to |
| `CLAW_SERVER_PORT` | no | `8000` | TCP port for `ledger serve` to listen on |
| `CLAW_API_SECRET` | for serve | — | Bearer token for the HTTP API; server refuses to start if unset |
| `PLAID_WEBHOOK_SECRET` | for webhooks | — | Shared secret for Plaid HMAC-SHA256 signature verification |
| `CLAW_LOG_LEVEL` | no | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `OPENCLAW_HOOKS_URL` | no | `http://127.0.0.1:18789/hooks/agent` | OpenClaw notification endpoint |
| `OPENCLAW_HOOKS_TOKEN` | no | — | Bearer token for OpenClaw; leave unset to disable notifications |
| `OPENCLAW_HOOKS_AGENT` | no | `Hestia` | Name of the OpenClaw ingestion agent to wake after a sync |
| `OPENCLAW_HOOKS_WAKE_MODE` | no | `now` | Wake mode passed to OpenClaw |
| `CLAW_SCHEDULED_SYNC_ENABLED` | no | `false` | Enable the scheduled sync fallback loop; set to `true` to activate |
| `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` | no | `24` | Hours of sync silence before an item is treated as overdue; minimum 1 |

Notes:

- `PLAID_ENV` should usually stay `sandbox` during local development.
- `CLAW_PLAID_LEDGER_DB_PATH` should point to a local SQLite file.
- `CLAW_PLAID_LEDGER_WORKSPACE_PATH` should be set only when OpenClaw
  exports are being used.
- `CLAW_PLAID_LEDGER_ITEM_ID` is a single string that identifies which Plaid
  item (institution link) this sync run belongs to. Defaults to
  `"default-item"`. For multi-institution households, run `sync` once per
  institution with a distinct value. Do not use comma-separated lists.
- `OPENCLAW_HOOKS_TOKEN` enables OpenClaw wake notifications. Leave it unset
  to disable silently; a warning is logged but nothing crashes.

## CLI commands

| Command | Description |
|---|---|
| `ledger doctor` | Validates config, DB connectivity, schema, and row counts; checks OpenClaw notification config |
| `ledger doctor --production-preflight` | Validates live-readiness config without contacting external services; exits non-zero on any required failure |
| `ledger init-db` | Creates the SQLite database and initialises the schema (safe to re-run) |
| `ledger items` | Shows per-item health (token presence, account count, last sync) for all entries in `items.toml`; exits 0 always |
| `ledger link` | Connects a Plaid institution via browser and prints the resulting `access_token` and `items.toml` snippet |
| `ledger apply-precedence` | Applies source-precedence mappings from `items.toml` (`suppressed_accounts`) into the DB and clears stale mappings |
| `ledger overlaps` | Shows configured source-precedence status and potential unconfirmed overlaps across items |
| `ledger sync` | Fetches transactions from Plaid and persists them to SQLite; `sync --all` is the standard household path |
| `ledger serve` | Starts the FastAPI/uvicorn HTTP server; binds to `CLAW_SERVER_HOST:CLAW_SERVER_PORT` (default `127.0.0.1:8000`) |

## HTTP API

`ledger serve` exposes a REST API for OpenClaw agents:

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check; no auth required |
| `POST` | `/webhooks/plaid` | Receives Plaid webhook events; triggers background sync on `SYNC_UPDATES_AVAILABLE` |
| `GET` | `/transactions` | Paginated, filterable transaction list; includes `annotation` per row (`null` if unannotated); accepts `range` shorthand (`last_month`, `this_month`, `last_30_days`, `last_7_days`) or explicit `start_date`/`end_date`; defaults to canonical view |
| `GET` | `/spend` | Aggregate spend total/count for a date window or named `range` shorthand (`last_month`, `this_month`, `last_30_days`, `last_7_days`) with optional `owner`, `tags`, `account_id`, `category`, `tag`, `include_pending`, and `view` filters |
| `GET` | `/categories` | Distinct sorted category values from all annotations |
| `GET` | `/tags` | Distinct sorted tag values from all annotations |
| `GET` | `/accounts` | All synced accounts with human-readable labels (`label`, `description`) from `account_labels`; use to discover account IDs |
| `PUT` | `/accounts/{id}` | Upsert a label/description for an account; returns full account record; 404 for unknown IDs |
| `GET` | `/transactions/{id}` | Single transaction with merged annotation and suppression provenance (`suppressed_by`) |
| `PUT` | `/annotations/{id}` | Upsert annotation; returns the full updated transaction record (no follow-up GET needed) |
| `GET` | `/openapi.json` | Auto-generated OpenAPI spec |
| `GET` | `/docs` | Swagger UI |

All endpoints except `/health`, `/openapi.json`, and `/docs` require
`Authorization: Bearer <CLAW_API_SECRET>`.

After a webhook-triggered sync that adds, modifies, or removes at least one
transaction, the server sends a `POST` to the OpenClaw `/hooks/agent` endpoint
to wake the configured ingestion agent (Hestia by default). Athena analysis
runs later on schedule or anomaly-triggered follow-up. Set
`OPENCLAW_HOOKS_TOKEN` to enable this; leave it unset to disable silently.

## Observability and tracing

- Every API response includes `X-Request-Id`.
- Request log lines use `request_id` values (`req-xxxxxxxx`).
- Sync log lines use `sync_run_id` values (`sync-xxxxxxxx`) for CLI, scheduled,
  and webhook-triggered sync runs.
- DEBUG webhook payload logs are redacted; bearer tokens, Plaid secrets, and
  access tokens are never logged.

## Production preflight

Before using live Plaid credentials for the first time, run the
production preflight to verify your environment is correctly configured:

```bash
uv run ledger doctor --production-preflight
```

All required checks must show `[PASS]` before running a live sync.
A `[WARN]` on `PLAID_ENV_SANDBOX` means `PLAID_ENV` is still set to
`sandbox` — update it to `production` for live bank connections.

See `RUNBOOK.md` for the full production onboarding checklist.

## Household source precedence

For shared-account households, keep raw ingestion complete and configure
canonical suppression in `items.toml` using `[[items.suppressed_accounts]]`.
Then run:

```bash
uv run ledger apply-precedence
uv run ledger overlaps
```

`GET /transactions` defaults to `view=canonical` (suppressed-account
transactions hidden). Use `GET /transactions?view=raw` when you need the
full raw dataset for audits.


## Example

After creating your config:

```bash
uv run ledger init-db
uv run ledger doctor
uv run ledger sync
uv run ledger serve   # starts API server on http://127.0.0.1:8000
```


## Quality defaults

- Maximum line length: **79 characters** for Python source
- Ruff linting is configured to be strict (`select = ["ALL"]`) with minimal,
  documented exceptions
- Mypy runs in strict mode for source code
- Tests use pytest and should accompany behavior changes
- Markdown files are documentation and are not part of lint/type checks

See `ARCHITECTURE.md` for structure and quality standards.

## Continuous integration

GitHub Actions runs `ruff`, `mypy`, and `pytest` on every pull request
and on every push to `master` (including merged PRs).

## AI contributor policy

AI coding agents must run the full quality gate before committing.
See `AGENTS.md` and `CONTRIBUTING.md` for mandatory rules and
hook installation.
