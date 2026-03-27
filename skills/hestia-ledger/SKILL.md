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
        - ~/.openclaw/.env
    primaryEnv: CLAW_API_SECRET
---

## Setup

```bash
cat >> ~/.openclaw/.env <<'EOF'
CLAW_API_SECRET=<your-CLAW_API_SECRET-value>
CLAW_LEDGER_URL=http://127.0.0.1:8000
EOF
chmod 600 ~/.openclaw/.env
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
3. `GET /categories` — discover existing category vocabulary before writing
4. `GET /tags` — discover existing tag vocabulary before writing
5. `PUT /transactions/{transaction_id}/allocations` — **primary write surface
   for category/tags/note.** Atomically replaces all allocations. Works for
   both unsplit and split transactions. Request body is a JSON array of
   `{amount, category?, tags?, note?}` items. Response is the full transaction
   detail with `"allocations": [...]` — no follow-up GET needed to confirm
   written state.
6. `PUT /annotations/{transaction_id}` — **compatibility shim: single-allocation
   transactions only.** Returns HTTP 409 if the transaction has been split.
   Prefer item 5 (`PUT /transactions/{id}/allocations`) for all writes.
7. `GET /accounts` — retrieve all known accounts with human-readable labels
8. `PUT /accounts/{account_id}` — write or update a label for an account
9. `GET /errors` — recent ledger warnings and errors; use as a pre-run
   health check before each ingestion run

`GET /spend` is Athena-owned unless an operator explicitly asks Hestia to run
one-off diagnostics.

## Allocation response shapes

The list and detail endpoints return different shapes. Always check context.

### List view — `GET /transactions`

Each row includes `"allocation": {...}` (singular object). One row per
(transaction, allocation) pair. `category`, `tags`, and `note` may be null
for uncategorized transactions.

```json
"allocation": {
  "id": 1,
  "amount": 50.00,
  "category": "groceries",
  "tags": ["household"],
  "note": "weekly shopping",
  "updated_at": "2026-03-25T10:00:00+00:00"
}
```

Use `allocation.category`, `allocation.tags`, `allocation.note` when working
with list results. The `allocation` key is always present (never null).

For split transactions, the same transaction `id` appears once per allocation
in list results.

### Detail view — `GET /transactions/{id}` and `PUT /transactions/{id}/allocations`

These endpoints return `"allocations": [...]` (array, never null). For unsplit
transactions the array has exactly one element; for split transactions it
contains all allocations ordered by `id ASC`.

```json
"allocations": [
  {
    "id": 5,
    "amount": 60.00,
    "category": "groceries",
    "tags": ["household"],
    "note": "weekly shopping",
    "updated_at": "2026-03-25T10:00:00+00:00"
  }
]
```

Use `allocations[0].category` etc. when working with detail-view results.
Check `allocations.length` to detect split transactions.

## Pagination

`GET /transactions` supports offset-based pagination via `limit` and `offset`
query parameters. Every list response includes:

```json
{
  "transactions": [...],
  "total": 247,
  "limit": 100,
  "offset": 0
}
```

- `limit` — number of rows per page; default `100`, maximum `500`.
- `offset` — zero-based row index of the first row on this page.
- `total` — total number of rows matching the query (independent of limit/offset).

**To paginate to completion:**

1. Start with `offset=0` and a fixed `limit` (e.g. `100`). Keep `limit` stable
   within a run.
2. After each response, advance: `offset += limit`.
3. Stop when `offset >= total` — the next page would be empty.

Equivalently: stop when the number of rows returned is less than `limit`
(the server returned a partial page, meaning this was the last).

**If pagination is interrupted** (call fails or run is aborted mid-way),
report partial coverage and avoid definitive completeness claims.

## Deterministic ingestion loop

For each run:

0. **Pre-run health check.** Call `GET /errors?hours=1&min_severity=ERROR`
   before starting ingestion. If the response contains any ERROR-level rows,
   surface them in the run frame output and lower overall confidence for
   the run. Do not abort — continue ingestion with reduced confidence and
   flag any affected results.
1. Pin a deterministic query frame (`start_date`, `end_date`, fixed page size).
2. Query `GET /transactions` in `view=canonical` and paginate to completion.
   Each row includes a nested `allocation` field (singular, list-view shape).
   Use `allocation.category`, `allocation.tags`, and `allocation.note` to
   screen for missing or stale categorization. The `allocation` object is
   always present (never null); `category`, `tags`, and `note` within it may
   be null for uncategorized transactions. If the same transaction `id` appears
   in multiple rows, it has been split — drill down before writing.
3. Identify candidates that are pending, missing expected tags/notes, or marked
   for re-review.
4. Re-fetch each candidate with `GET /transactions/{id}` before any write.
   The detail response contains `"allocations": [...]`. Check
   `allocations.length`: if `> 1`, the transaction has been split by an
   operator — **do not overwrite the split; flag for Athena review instead.**
5. Write `PUT /transactions/{transaction_id}/allocations` only when evidence
   is specific. This endpoint works for both unsplit and split transactions.
   Do not use `PUT /annotations/{id}` — it returns 409 for split transactions.
6. If confidence is low, set the allocation with `needs-athena-review` in
   `tags` and continue.

## API guardrails

### Account identity

- Call `GET /accounts` when account identity context is needed (e.g. to
  map an opaque Plaid account ID to a human-readable label for annotation
  purposes).
- Call `PUT /accounts/{account_id}` to apply a label when the operator has
  provided one. Only label accounts that already exist in the `accounts` table
  (the endpoint returns 404 for unknown IDs).

### Vocabulary hygiene

- Call `GET /categories` and `GET /tags` at the start of each ingestion run
  to load the current vocabulary before writing any annotations.
- Reuse existing category and tag values; do not invent near-duplicates.

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
3. Queue records where `allocation.category`, `allocation.tags`, and
   `allocation.note` are null or stale, or where categorization shows
   uncertainty. The `allocation` field (singular, list-view shape) is included
   in every list row — no drill-down needed for initial screening.
   Note: if the same transaction `id` appears in multiple list rows, it has
   been split. Drill down with `GET /transactions/{id}` before any write.

### 2) Drill-down before allocation write

1. Run `GET /transactions/{id}`.
2. Verify amount, date, pending/posting state, owner context, and existing
   allocations: check `allocations.length` and fields on each element
   (`allocations[0].category`, `allocations[0].tags`, `allocations[0].note`).
3. **If `allocations.length > 1`**: the transaction has been split by an
   operator. Do not overwrite. Flag for Athena review with
   `needs-athena-review` on the existing allocation (use
   `PUT /transactions/{id}/allocations` preserving all existing allocations
   plus the new tag). Or escalate without writing.
4. If conflicting context remains, run a filtered `GET /transactions` query.
5. Write `PUT /transactions/{transaction_id}/allocations` only when evidence
   is sufficient. The response contains the full transaction record with the
   updated `"allocations": [...]` array — no follow-up GET required.

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

## Allocation write policy

Write allocations only when all are true:

- transaction was re-fetched in current run (`GET /transactions/{id}`),
- `allocations.length == 1` OR the write intentionally preserves/extends
  an operator-defined split (never silently discard split allocations),
- note/tag is factual and evidence-based,
- allocation write improves downstream review.

Abstain from non-escalation writes when:

- evidence is ambiguous/conflicting,
- the transaction cannot be re-fetched,
- confidence is below threshold,
- the transaction has `allocations.length > 1` and the intent is unclear
  (flag for Athena instead).

Always use `PUT /transactions/{id}/allocations` for writes. Do not use
`PUT /annotations/{id}` — it is a compatibility shim that returns 409 for
split transactions.

### Required allocation shape

- `tags`: lowercase kebab-case labels.
- `note`: concise rationale with observed signal + timeframe.
- optional `category`: only when confidently known; use `GET /categories`
  vocabulary.

For uncertain cases, include `needs-athena-review` in `tags`.

## Response contract

Hestia outputs are operational and machine-checkable:

1. **Run frame**: queried window, pagination status, and filters.
2. **Actions taken**: transaction IDs annotated + exact tags written.
3. **Escalations**: transaction IDs tagged `needs-athena-review` + reason.
4. **Gaps**: failed calls, partial coverage, or unresolved ambiguity.

## Companion files

- `checklists/allocation_write_checklist.md`
- `checklists/query_playbooks.md`
