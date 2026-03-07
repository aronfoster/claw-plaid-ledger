# Architecture

## Components

- Plaid client
- Sync engine
- SQLite ledger
- Exporter
- OpenClaw notifier
- Config/secrets layer
- CLI boundary (`Typer`) for all operator workflows

## Data flow

Plaid -> sync engine -> SQLite -> markdown exporter -> OpenClaw trigger

## Boundaries

- Secrets stay outside workspace
- SQLite is source of truth
- Markdown is a projection for agent consumption
- OpenClaw is invoked only after deterministic ingestion completes

## Key entities

- account
- transaction
- sync_state
- review_item
- rule

## Interfaces

- `sync`
- `export`
- `notify`
- `reconcile`
- `doctor`

## Runtime and tooling standards

- Python: 3.12+
- Environment/dependency management: `uv`
- CLI framework: `Typer`
- Datastore: standard-library `sqlite3`
- Testing: `pytest`
- Formatting/linting: `ruff format` + `ruff check`
- Type-checking: `mypy --strict`

## Repository layout

```text
src/claw_plaid_ledger/
  __init__.py
  cli.py

tests/
  test_cli.py

pyproject.toml
README.md
ARCHITECTURE.md
VISION.md
ROADMAP.md
SPRINT.md
```

## Quality gate

A change is ready to merge when all are true. These gates
apply to Python code; Markdown is documentation-only:

1. `uv run ruff format . --check` passes.
2. `uv run ruff check .` passes.
3. `uv run mypy` passes.
4. `uv run pytest` passes.
