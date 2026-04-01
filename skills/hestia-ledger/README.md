# Hestia Ledger Skill Bundle

This directory is a copy-ready OpenClaw skill bundle for Hestia's ingestion
loop.

## What is in this bundle

- `SKILL.md` — ingestion-only contract for deterministic allocation updates
  and escalation.
- `checklists/allocation_write_checklist.md` — pre-write checklist for
  `PUT /transactions/{id}/allocations`.
- `checklists/query_playbooks.md` — ingestion query sequences, escalation
  tagging rules, and failure handling.

## How to copy into a new OpenClaw workspace

Copy the **entire `hestia-ledger` directory** into your target skill location,
for example:

```bash
cp -R hestia-ledger "$OPENCLAW_SKILLS_DIR/hestia-ledger"
```

## Runtime profile

- **Primary trigger:** event-driven (e.g., Plaid sync complete notification).
- **Expected cadence:** frequent / low-latency.
- **Output style:** operational status and escalation tags.

## Minimum environment assumptions

1. Access to ledger API with bearer-token auth.
2. Endpoints: `GET /transactions`, `GET /transactions/{id}`,
   `PUT /transactions/{id}/allocations`.
3. Canonical-source precedence configured by operators.
4. Stable timezone/basis for repeatable date-window queries.
