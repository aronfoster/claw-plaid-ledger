# Roadmap

## M0 - Project skeleton
Repo structure, config, logging, CLI entrypoints, docs.

## M1 - Plaid connection and initial sync
Support Plaid Link flow and first transaction import.

## M2 - Local ledger
Persist transactions, accounts, and sync cursor in SQLite.

## M3 - Agent-friendly exports
Write markdown summaries/inbox files into OpenClaw workspace.

## M4 - Change-triggered notification
Wake OpenClaw only when new or changed transactions require review.

## M5 - Basic intelligence
Rules for merchant normalization, category hints, pending/posting reconciliation.

## M6 - OSS hardening
Install docs, sample config, tests, packaging, security notes.
