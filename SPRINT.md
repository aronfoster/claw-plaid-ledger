# Sprint 1

## Sprint goal

Establish the minimum vertical slice: import transactions from Plaid into
SQLite.

## In progress

- [x] Define project layout
- [x] Establish Python 3.12 + uv tooling baseline
- [x] Add Typer CLI entrypoint with `doctor` command
- [x] Configure strict quality tooling (ruff, mypy, pytest)
- [ ] Add config loader
- [ ] Create SQLite schema
- [ ] Implement initial Plaid client wrapper
- [ ] Implement first sync command
- [ ] Add basic logging
- [ ] Add tests for schema and sync state

## Acceptance criteria

- User can configure Plaid credentials locally
- Running sync creates a SQLite DB
- Transactions are stored with stable unique IDs
- Sync cursor is persisted
- Re-running sync does not duplicate transactions

## Deferred

- Markdown exports
- OpenClaw wake-up
- Merchant rules
- Webhook support
