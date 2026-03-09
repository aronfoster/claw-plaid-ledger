# Roadmap

## M0 - Project skeleton

Repo structure, config, logging, CLI entrypoints, docs.

### Status

Complete. Python + uv baseline, strict quality tooling, environment-backed
configuration, and SQLite schema bootstrap are in place.

## M1 - Plaid connection and initial sync

Add Plaid client integration and implement the first transaction sync path into
SQLite with cursor-based idempotent reruns.

## M2 - Local ledger hardening

Expand deterministic persistence behavior for accounts, transactions, and sync
state; improve operational diagnostics.

## M3 - Agent-friendly exports

Write markdown summaries/inbox files into OpenClaw workspace.

## M4 - Change-triggered notification

Wake OpenClaw only when new or changed transactions require review.

## M5 - Basic intelligence

Rules for merchant normalization, category hints, pending/posting
reconciliation.

## M6 - OSS hardening

Install docs, sample config, tests, packaging, security notes.

## Future / unscheduled

### Multi-institution sync UX

`CLAW_PLAID_LEDGER_ITEM_ID` is a single string set per invocation. A
household with more than one institution must currently run `ledger sync`
multiple times, each time with a different value for that variable (e.g.
via separate `.env` files or wrapper scripts). This is functional but
operationally tedious.

Future work should add first-class multi-institution support, for example:

- A config file (TOML or similar) that declares multiple named items, each
  with its own `access_token` and `item_id`, so a single `ledger sync`
  invocation iterates over all of them.
- Or a `ledger sync --all` flag that reads all configured items and syncs
  each in sequence, producing a combined summary.

Until this is addressed, operators with multiple institutions should create
one wrapper script per item and call them from a single top-level script.
