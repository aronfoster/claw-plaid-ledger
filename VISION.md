# Vision

## One-liner

Local-first household finance ingestion for OpenClaw.

## Problem

Polling an agent to repeatedly inspect mostly unchanged finance data wastes
tokens and mixes deterministic ingestion with interpretation.

## Goal

Use Plaid to ingest transaction data into a local ledger, then wake OpenClaw
only when there is new or uncertain information worth interpreting.

## Principles

- Local-first
- Deterministic ingestion
- Agent for interpretation, not bookkeeping
- Human-readable exports
- Minimal secrets exposure
- Security-first defaults
- Small, composable architecture

## Non-goals

- Security theater without concrete controls
- Full budgeting UI
- Multi-user SaaS
- Tax software
- Investment analytics
- Bank credential storage in agent workspace
