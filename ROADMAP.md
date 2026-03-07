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
