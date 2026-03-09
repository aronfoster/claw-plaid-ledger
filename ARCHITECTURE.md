# Architecture

## Current milestone focus

M2 (Local ledger hardening) is complete. The repository is now in Sprint 4
with a focus on agent-friendly exports (M3).

- Write markdown summaries and inbox files into the OpenClaw workspace
- Make exports idempotent and safe to regenerate on every sync run

Merchant normalization, review queues, and notification triggering are
planned but intentionally deferred until later milestones.

## Components

- CLI boundary (`typer` library) for operator workflows
- Config/secrets layer (`config.py`)
- SQLite bootstrap and persistence layer (`db.py` + `schema.sql`)
- Plaid client wrapper (`plaid_adapter.py`)
- Sync engine (`sync_engine.py`)

## Data flow

Plaid API -> sync engine -> SQLite -> (planned) markdown export -> OpenClaw workspace

## Boundaries

- Secrets stay outside the workspace and are loaded via environment variables.
- SQLite is the source of truth for local financial state.
- Database writes should be deterministic and idempotent across reruns.
- CLI commands orchestrate workflows but should not contain raw Plaid API setup.
- Markdown exports in the OpenClaw workspace are derived views of SQLite data;
  they are overwritten on each export run and never treated as source of truth.

## Key entities

- `account`
- `transaction`
- `sync_state`

Deferred entities (`review_item`, rules) land in later phases.

## Interfaces

Current operator-facing CLI commands:

- `doctor` — validates config, DB connectivity, schema, and reports row counts;
  with `--verbose` shows redacted config values
- `init-db` — creates the SQLite database and initializes the schema
- `sync` — fetches transactions from Plaid and persists them to SQLite;
  respects `CLAW_PLAID_LEDGER_ITEM_ID` for multi-institution households

Planned in M3:

- `export` — writes markdown transaction summaries into the OpenClaw workspace

Deferred interfaces:

- `notify`
- `reconcile`

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

## Runtime and tooling standards

- Python: 3.12+
- Environment/dependency management: `uv`
- CLI framework: `typer` (real library, not a shim)
- Datastore: standard-library `sqlite3`
- Testing: `pytest`
- Formatting/linting: `ruff format` + `ruff check`
- Type-checking: `mypy --strict`

## Repository layout (current)

```text
src/claw_plaid_ledger/
  __init__.py
  cli.py
  config.py
  db.py
  plaid_adapter.py
  plaid_models.py
  schema.sql
  sync_engine.py

tests/
  test_cli.py
  test_config.py
  test_db.py
  test_plaid_adapter.py
  test_sync_engine.py

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
