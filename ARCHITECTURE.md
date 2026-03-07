# Architecture

## Components
- Plaid client
- Sync engine
- SQLite ledger
- Exporter
- OpenClaw notifier
- Config/secrets layer

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
