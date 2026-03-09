# Vision

## One-liner

Local-first household finance server: deterministic ingestion from Plaid,
safe agent access via API, human-readable exports on demand.

## Problem

Polling an agent to repeatedly inspect mostly unchanged finance data wastes
tokens and mixes deterministic ingestion with interpretation. Giving agents
direct database access creates safety and correctness risks.

## Goal

Run a persistent local server that ingests transaction data from Plaid via
webhooks, exposes a typed API for OpenClaw agents to query and annotate,
and wakes OpenClaw only when there is new or uncertain information worth
interpreting.

## Principles

- Local-first
- Deterministic ingestion
- Server as the runtime; CLI as the ops interface
- Agent for interpretation, not bookkeeping
- Agents interact through the API, never directly with SQLite
- Secure by default, even on a trusted local machine
- Minimal secrets exposure
- Small, composable architecture

## Non-goals

- Security theater without concrete controls
- Full budgeting UI
- Multi-user SaaS
- Tax software
- Investment analytics
- Bank credential storage in agent workspace
