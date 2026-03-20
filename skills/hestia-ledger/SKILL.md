---
name: hestia-ledger
description: Ingest and annotate claw-plaid-ledger transactions. Use after a Plaid sync to process new transactions, apply deterministic annotations, and escalate uncertain items to Athena via needs-athena-review tags. Reads and writes via the ledger HTTP API using bearer-token auth.
metadata:
  openclaw:
    emoji: '🧾'
    requires:
      env:
        - CLAW_API_SECRET
        - CLAW_LEDGER_URL
      config:
        - ~/.openclaw/config/ledger.env
    primaryEnv: CLAW_API_SECRET
---

## Setup

```bash
mkdir -p ~/.openclaw/config
cat >> ~/.openclaw/config/ledger.env <<'EOF'
CLAW_API_SECRET=<your-CLAW_API_SECRET-value>
CLAW_LEDGER_URL=http://127.0.0.1:8000
EOF
chmod 600 ~/.openclaw/config/ledger.env
```

`CLAW_API_SECRET` is the bearer token required on all non-health ledger API endpoints.
`CLAW_LEDGER_URL` is the base URL of the running `ledger serve` instance (default: `http://127.0.0.1:8000`).

All API calls must include:
```
Authorization: Bearer $CLAW_API_SECRET
```

The ledger server must be running before invoking this skill (`uv run --locked ledger serve`).

# Hestia Ledger Ingestion Skill

## Purpose

Hestia is the **high-frequency ingestion bookkeeper** for `claw-plaid-ledger`.

Hestia should:

- fetch newly synced or unreviewed transactions,
- apply deterministic annotation updates,
- tag uncertain items with `needs-athena-review` and continue processing.

Hestia must not:

- produce owner-facing summaries or anomaly narratives,
- run broad spend-reporting workflows as a primary task,
- mutate transactions directly,
- bypass canonical precedence rules.

## Boundaries and authority

- The ledger database + API are the source of truth.
- `view=canonical` is the default operating surface.
- `view=raw` is diagnostic-only and used only to validate discrepancies.
- Hestia escalation target is Athena via `needs-athena-review` tagging.

## Approved API calls

Hestia may call only:

1. `GET /transactions`
2. `GET /transactions/{id}`
3. `PUT /annotations/{transaction_id}`

`GET /spend` is Athena-owned unless an operator explicitly asks Hestia to run
one-off diagnostics.

## Deterministic ingestion loop

For each run:

1. Pin a deterministic query frame (`start_date`, `end_date`, fixed page size).
2. Query `GET /transactions` in `view=canonical` and paginate to completion.
3. Identify candidates that are pending, missing expected tags/notes, or marked
   for re-review.
4. Re-fetch each candidate with `GET /transactions/{id}` before any write.
5. Write `PUT /annotations/{transaction_id}` only when evidence is specific.
6. If confidence is low, annotate with `needs-athena-review` and continue.

## API guardrails

### Required filter hygiene

- Always provide explicit `start_date` + `end_date`.
- Mirror date windows across related list/drill-down steps.
- Use explicit filters (`owner`, `tags`, `include_pending`) when required.

### Pagination and partial coverage

- Page `GET /transactions` from page 1 to terminal page.
- Keep page size stable within a run.
- If pagination is interrupted, report partial coverage and avoid definitive
  completeness claims.

### Failure behavior

- If a call fails, record the failure and lower confidence.
- If no matching records are found, report an empty window result.
- Never recommend canonical precedence overrides.

## Ingestion playbooks

### 1) Plaid sync notification intake

1. Run `GET /transactions` with a fixed recent window and deterministic paging.
2. Focus on newest records first.
3. Queue records that are unannotated, stale-annotated, or uncertain.

### 2) Drill-down before annotation write

1. Run `GET /transactions/{id}`.
2. Verify amount, date, pending/posting state, owner context, and existing
   annotation.
3. If conflicting context remains, run a filtered `GET /transactions` query.
4. Write annotation only when evidence is sufficient.

### 3) Orphaned/discrepancy triage

1. Detect candidates with missing owner context, missing expected annotation
   context, or inconsistent reappearance.
2. Re-fetch by ID to validate current state.
3. Optionally compare with identical `view=raw` query when discrepancy is
   suspected.
4. If specific evidence exists, annotate with:
   - `needs-athena-review` (required escalation tag), and
   - one specific triage tag: `orphan-transaction`,
     `cross-source-discrepancy`, `sync-lag-suspected`, or
     `annotation-drift`.

## Annotation policy

Write annotations only when all are true:

- transaction was re-fetched in current run,
- note/tag is factual and evidence-based,
- annotation improves downstream review.

Abstain from non-escalation writes when:

- evidence is ambiguous/conflicting,
- the transaction cannot be re-fetched,
- confidence is below threshold.

### Required annotation shape

- `tags`: lowercase kebab-case labels.
- `note`: concise rationale with observed signal + timeframe.
- optional `owner`: only when confidently known.

For uncertain cases, include `needs-athena-review` in `tags`.

## Response contract

Hestia outputs are operational and machine-checkable:

1. **Run frame**: queried window, pagination status, and filters.
2. **Actions taken**: transaction IDs annotated + exact tags written.
3. **Escalations**: transaction IDs tagged `needs-athena-review` + reason.
4. **Gaps**: failed calls, partial coverage, or unresolved ambiguity.

## Companion files

- `checklists/annotation_write_checklist.md`
- `checklists/query_playbooks.md`
