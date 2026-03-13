# Hestia Ledger Skill Bundle

This directory is a copy-ready OpenClaw skill bundle for Hestia's ledger work.

## What is in this bundle

- `SKILL.md` — operational contract for how Hestia queries the ledger API and
  writes annotations safely.
- `checklists/annotation_write_checklist.md` — fast checklist to run before any
  `PUT /annotations/{transaction_id}` write.
- `checklists/query_playbooks.md` — quick-reference intent-to-endpoint
  sequences and failure-handling guardrails.
- `templates/owner_summary_template.md` — structured output template for
  deterministic owner-aware household summaries.

## How to copy into a new OpenClaw workspace

Copy the **entire `hestia-ledger` directory** into your target skill location,
for example:

```bash
cp -R hestia-ledger "$OPENCLAW_SKILLS_DIR/hestia-ledger"
```

No internal links need editing if the directory contents stay together.

## Minimum environment assumptions

The target OpenClaw runtime must provide:

1. Access to this ledger API with bearer-token auth enabled.
2. Endpoints: `GET /transactions`, `GET /transactions/{id}`, `GET /spend`, and
   `PUT /annotations/{transaction_id}`.
3. Canonical-source precedence already configured by operators (Hestia must not
   override it).
4. A stable time basis (`today`/timezone) so repeated date-window queries are
   reproducible.
