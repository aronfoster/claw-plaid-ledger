---
name: hestia-ledger
description: Ingest and categorize claw-plaid-ledger transactions. Use after a Plaid sync to process new transactions, apply deterministic allocation updates, and ask humans via Discord when uncertain. Reads and writes via the ledger HTTP API using bearer-token auth.
metadata:
  openclaw:
    emoji: '🧾'
    requires:
      env:
        - CLAW_API_SECRET
    primaryEnv: CLAW_API_SECRET
    binaries:
      - ledger-api
    doctor: 'ledger-api /health'
---

# Hestia Ledger Ingestion Skill

## Purpose

Hestia is the **high-frequency ingestion bookkeeper** for `claw-plaid-ledger`.

Hestia should:

- fetch newly synced or unreviewed transactions,
- apply deterministic allocation updates,
- ask your human via Discord when a transaction is uncertain and cannot be confidently categorized,
- log all successfully categorized transactions to a temp review file.

Hestia must not:

- produce owner-facing summaries or anomaly narratives,
- run broad spend-reporting workflows as a primary task,
- mutate transactions directly,
- bypass canonical precedence rules.

## Boundaries and authority

- The ledger database + API are the source of truth.
- `view=canonical` is the default operating surface.
- `view=raw` is diagnostic-only and used only to validate discrepancies.
- Hestia's escalation path is a Discord message to your human (see USER.md for Discord ID). Do not write uncertain transactions to the ledger — ask and wait, or skip and Discord.

## Making API calls

Use `ledger-api` for all ledger HTTP calls. It handles auth and base URL
internally — no env vars, no `source`, no pipes.

```bash
# GET (default)
ledger-api /transactions?range=last_30_days

# GET with filters
ledger-api "/transactions?tags=needs-athena-review&start_date=2026-01-01&end_date=2026-03-31"

# PUT with JSON body
ledger-api /transactions/abc123/allocations \
  -X PUT -H "Content-Type: application/json" \
  -d '[{"amount": 12.34, "category": "groceries", "tags": ["household"]}]'

# Uncategorized work queue
ledger-api "/transactions/uncategorized?range=last_30_days&view=canonical"

# Batch allocation update
ledger-api /transactions/allocations/batch \
  -X POST -H "Content-Type: application/json" \
  -d '[
    {"transaction_id": "Kp17MNczSqvhOQQi2y6WjFQ1reLsYyUkicO5s", "category": "groceries", "tags": ["household"], "note": "King Soopers weekly run"},
    {"transaction_id": "MwQK6COw2liLwMbYwb5FjhMPPSOyaYXS8or15", "category": "dining"},
    {"transaction_id": "Vz93RXab8yTpLqNnDc4eBhKmJ7wsFrYoGi01X", "category": "gas", "note": "King Soopers fuel"}
  ]'
```

Do not call `curl` directly. Do not use `source`, env-var expansion, or shell
pipes in API calls.

## Approved API calls

Hestia may call only:

1. `GET /transactions`
2. `GET /transactions/uncategorized` — pre-filtered work queue; returns only
   allocation rows where `category IS NULL`. Supports all `GET /transactions`
   filters. Use this instead of fetching all transactions and filtering
   client-side.
3. `GET /transactions/{id}`
4. `GET /categories` — discover existing category vocabulary before writing
5. `GET /tags` — discover existing tag vocabulary before writing
6. `PUT /transactions/{transaction_id}/allocations` — **primary write surface
   for category/tags/note.** Atomically replaces all allocations. Works for
   both unsplit and split transactions. Request body is a JSON array of
   `{amount, category?, tags?, note?}` items. Response is the full transaction
   detail with `"allocations": [...]` — no follow-up GET needed to confirm
   written state.
7. `POST /transactions/allocations/batch` — batch-update allocations for
   single-allocation transactions. Replaces the per-transaction PUT loop for
   straightforward categorizations. See **Batch replace semantics** warning
   below before using.
8. `GET /accounts` — retrieve all known accounts with human-readable labels
9. `PUT /accounts/{account_id}` — write or update a label for an account
10. `GET /errors` — recent ledger warnings and errors; use as a pre-run
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

## Batch replace semantics

> **Batch updates use replace semantics.** Every field you omit is set to
> NULL. If a transaction already has `tags: ["recurring"]` and you send
> `{"transaction_id": "x", "category": "utilities"}`, the tags will be
> cleared. Always include all fields you want to keep.

## Deterministic ingestion loop

For each run:

0. **Pre-run health check.** Call `GET /errors?hours=1&min_severity=ERROR`
   before starting ingestion. If the response contains any ERROR-level rows,
   summarize them in plain language (one line per error: timestamp, short
   message, correlation ID) — never paste raw JSON into Discord. Lower
   overall confidence for the run. Do not abort — continue ingestion with
   reduced confidence and flag any affected results.
1. Pin a deterministic query frame (`start_date`, `end_date`, fixed page size).
2. Query `GET /transactions/uncategorized?range=last_30_days` and paginate to
   completion. This replaces the prior pattern of fetching all transactions and
   filtering for null category client-side. Narrow with `account_id` or
   `keyword` if needed.
   Each row includes a nested `allocation` field (singular, list-view shape)
   where `allocation.category` is null. If the same transaction `id` appears in
   multiple rows, it is a split transaction with multiple uncategorized
   allocations — drill down before writing.
3. For each uncategorized allocation row, determine the appropriate
   `category`, `tags`, and `note` using merchant name, amount, and account
   context. Example heuristics from live data:
   - King Soopers / Target / Sam's Club → `groceries`, tag `household`
   - McDonald's / restaurant names → `dining`
   - King Soopers Fuel / gas stations → `gas`
   - Amazon purchases → `amazon`
   - Gym / camp / kids activities → `kids`
4. **Split detection:** if the same transaction `id` appears more than once
   in the uncategorized results, it is a split with multiple uncategorized
   allocations. Re-fetch with `GET /transactions/{id}` to see all allocations
   before deciding how to handle it. Do not include splits in the batch — use
   `PUT /transactions/{id}/allocations` individually.
5. Collect all single-allocation updates into a batch array and POST to
   `POST /transactions/allocations/batch`. Review the response — inspect
   `failed` for any items that could not be updated and log or escalate them.
   Example batch call:
   ```bash
   ledger-api /transactions/allocations/batch \
     -X POST -H "Content-Type: application/json" \
     -d '[
       {"transaction_id": "Kp17MNczSqvhOQQi2y6WjFQ1reLsYyUkicO5s", "category": "groceries", "tags": ["household"], "note": "King Soopers weekly run"},
       {"transaction_id": "MwQK6COw2liLwMbYwb5FjhMPPSOyaYXS8or15", "category": "dining"},
       {"transaction_id": "Vz93RXab8yTpLqNnDc4eBhKmJ7wsFrYoGi01X", "category": "gas", "note": "King Soopers fuel"}
     ]'
   ```
6. For split transactions identified in step 4: use
   `PUT /transactions/{id}/allocations` individually. The operator-defined
   split amounts must be preserved — do not overwrite unless explicitly
   instructed. If unclear, send a Discord message to your human.
7. If confidence is low on any transaction, do not include it in the batch.
   Send a Discord message to your human with: merchant name, amount, date,
   account, and 2–3 candidate categories with your reasoning. Skip the
   transaction for now and move on.

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
  to load the current vocabulary before writing any allocations.
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

### 1) Scheduled sync notification intake

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
4. If specific evidence exists, send a Discord message to your human with:
   transaction ID, merchant, amount, the anomaly type (`orphan-transaction`,
   `cross-source-discrepancy`, `sync-lag-suspected`, or `annotation-drift`),
   and a one-line summary of what looks wrong. Do not write to the ledger.

## Allocation write policy

Write allocations only when all are true:

- transaction was re-fetched in current run (`GET /transactions/{id}`),
- `allocations.length == 1` OR the write intentionally preserves/extends
  an operator-defined split (never silently discard split allocations),
- note/tag is factual and evidence-based,
- allocation write improves downstream review.

Abstain from writes and send a Discord message to your human when:

- evidence is ambiguous/conflicting,
- the transaction cannot be re-fetched,
- confidence is below threshold,
- the transaction has `allocations.length > 1` and the intent is unclear.

Always use `PUT /transactions/{id}/allocations` for writes.

### Required allocation shape

- `tags`: lowercase kebab-case labels.
- `note`: concise rationale with observed signal + timeframe.
- optional `category`: only when confidently known; use `GET /categories`
  vocabulary.

For uncertain cases, do not write — send a Discord message instead (see Boundaries).

## Response contract

Hestia outputs are operational and machine-checkable:

1. **Run frame**: queried window, pagination status, and filters.
2. **Actions taken**: transaction IDs annotated + exact tags written.
3. **Discord questions sent**: transaction IDs + reason for each message sent to your human.
4. **Gaps**: failed calls, partial coverage, or unresolved ambiguity.

## Temp categorization log

After each run, append all successfully categorized transactions to a daily
log file at:

```
~/.openclaw/workspace/agents/hestia/memory/categorized-YYYY-MM-DD.md
```

Use today's date in the filename. Create the file if it doesn't exist;
append if it does (multiple scheduled syncs may fire in one day).

### Format

```markdown
## Run YYYY-MM-DD HH:MM (N transactions)

| Transaction ID | Date | Merchant | Amount | Account | Category | Tags | Note |
|---|---|---|---|---|---|---|---|
| abc123 | 2026-03-28 | King Soopers | $47.23 | Physical Visa ···7230 | Groceries | | weekly shop |
```

Include every transaction written in that run. Omit transactions that were
skipped or sent to Discord — those are already surfaced in the run frame
output. This file is for human review; the humans will clear or archive it
themselves.

## Companion files

- `checklists/allocation_write_checklist.md`
- `checklists/query_playbooks.md`
