# Architecture

## Sprint 2 implementation focus

This repository is currently in Sprint 2 with a narrow vertical slice:

- configure runtime from environment variables
- initialize and manage a local SQLite ledger
- add a thin Plaid ingestion path for accounts, transactions, and sync cursor
- keep repeated syncs deterministic and idempotent

Markdown exports, OpenClaw notifications, and reconciliation workflows are
planned but intentionally deferred until later milestones.

## Components

- CLI boundary (`Typer`) for operator workflows
- Config/secrets layer (`config.py`)
- SQLite bootstrap and persistence layer (`db.py` + `schema.sql`)
- Plaid client wrapper (Sprint 2 scope)
- Sync engine (Sprint 2 scope)

## Data flow (Sprint 2)

Plaid API -> sync engine -> SQLite

## Boundaries

- Secrets stay outside the workspace and are loaded via environment variables.
- SQLite is the source of truth for local financial state.
- Database writes should be deterministic and idempotent across reruns.
- CLI commands orchestrate workflows but should not contain raw Plaid API setup.

## Key entities

- `account`
- `transaction`
- `sync_state`

Deferred entities (`review_item`, rules) land in later phases.

## Interfaces

Current operator-facing interfaces:

- `doctor`
- `init-db`

Planned in Sprint 2:

- `sync`

Deferred interfaces:

- `export`
- `notify`
- `reconcile`

## Runtime and tooling standards

- Python: 3.12+
- Environment/dependency management: `uv`
- CLI framework: `Typer`
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
  schema.sql

src/
  typer.py

tests/
  test_cli.py
  test_config.py
  test_db.py

pyproject.toml
README.md
ARCHITECTURE.md
VISION.md
ROADMAP.md
SPRINT.md
```

## Quality gate

A change is ready to merge only when all required checks pass:

1. `uv run --locked ruff format . --check`
2. `uv run --locked ruff check .`
3. `uv run --locked mypy .`
4. `uv run --locked pytest`
