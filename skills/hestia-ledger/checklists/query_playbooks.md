# Hestia Ingestion Query Playbooks

Use this sheet for deterministic ingestion and escalation-only behavior.

## Global rules

- Default to `view=canonical`.
- Use `view=raw` only for discrepancy diagnostics over identical filters.
- Always set explicit `start_date` + `end_date`.
- Use deterministic pagination for `GET /transactions`.
- Never recommend canonical precedence overrides.

## Pagination mechanics

Response shape for `GET /transactions`:
`{ "transactions": [...], "total": N, "limit": L, "offset": O }`

- Advance: `offset += limit` after each page.
- Stop: when `offset >= total`.
- Keep `limit` stable within a run (recommended: `100`).
- If interrupted: report partial coverage; do not make totals claims.

## Vocabulary setup (start of run)

Before writing any allocations, load the current vocabulary:

1. `GET /categories` — retrieve existing category values (sorted).
2. `GET /tags` — retrieve existing tag values (sorted).
3. `GET /accounts` — retrieve account ID-to-label mapping for any
   account-specific annotation context.
4. `GET /errors?hours=1&min_severity=ERROR` — check for recent server errors
   before beginning ingestion. Surface any ERROR rows in the run frame; lower
   confidence if present.

Reuse these values; do not create near-duplicates.

## Ingestion playbooks

### 1) Sync intake and candidate detection

1. `GET /transactions` with fixed window + `view=canonical`. To re-review
   allocations already assigned to named categories (e.g. cleaning up
   `dining` and `groceries` rules), pass repeated `?category=` params:
   `GET /transactions?range=last_30_days&category=dining&category=groceries`
   uses OR semantics across categories and returns only the matching
   allocation rows — split transactions contribute only their matching
   allocation rows. Do not post-filter categories client-side.
2. Paginate to completion.
3. Each row includes an `allocation` field (singular, list-view shape). Use
   `allocation.category`, `allocation.tags`, and `allocation.note` to screen
   for missing or stale categorization. The `allocation` object is always
   present (never null); fields within it may be null for uncategorized
   transactions. No per-record drill-down needed for initial screening.
   If the same transaction `id` appears in multiple list rows, it has been
   split — drill down with `GET /transactions/{id}` before any write.

### 1b) Uncategorized work queue + batch write

Preferred pattern when the run goal is purely categorization (no stale-tag
or re-review work). Use instead of — or after — playbook 1.

Supported filters for `GET /transactions/uncategorized`:
`start_date`, `end_date`, `range`, `account_id`, `pending`, `min_amount`,
`max_amount`, `keyword`, `view`, `limit`, `offset`, `search_notes`, `tags`.

`GET /transactions/uncategorized` does **not** accept named `category`
filters — `?category=<name>` returns HTTP 422 because the queue means
`allocation.category IS NULL`. To re-review allocations already assigned
to one or more named categories, use `GET /transactions?category=...`
(repeatable; OR semantics across categories) instead.

1. `GET /transactions/uncategorized?range=last_30_days` — paginate to
   completion. Add `account_id=<id>` to scope to one account, `pending=true`
   to include pending charges, or `keyword=<merchant>` to narrow by merchant.
2. Classify each row. Check for split signals: if the same transaction `id`
   appears more than once, it has multiple uncategorized allocations — move
   it to the split path (step 4 below).
3. Build a batch array of single-allocation updates and POST to
   `POST /transactions/allocations/batch`. Inspect `failed` in the response
   and log or escalate any failures.
4. For splits: re-fetch with `GET /transactions/{id}` and use
   `PUT /transactions/{id}/allocations` individually — do not include splits
   in the batch.

### 2) Drill-down before write

1. `GET /transactions/{id}` to verify current details. The response contains
   `"allocations": [...]` (array). Check `allocations.length`:
   - `== 1`: unsplit transaction, safe to write.
   - `> 1`: operator-split transaction — **do not overwrite the split** unless
     explicitly instructed; flag for Athena review instead.
2. Optional filtered `GET /transactions` to resolve conflicting context.
3. `PUT /transactions/{transaction_id}/allocations` only if evidence is
   sufficient. This is the correct endpoint for all writes — it handles
   both unsplit and split transactions.
4. The PUT response contains the full updated transaction record with an
   `"allocations": [...]` array — use it directly to confirm the written state
   (no follow-up GET needed).

### 3) Orphan/discrepancy triage with Athena escalation

1. `GET /transactions` over fixed window and detect orphan/discrepancy signals.
2. `GET /transactions/{id}` for each candidate.
3. Optional `GET /transactions` with `view=raw` using identical filters.
4. If writing, include `needs-athena-review` plus one specific triage tag.
5. Continue ingestion loop; do not stop entire run for a single uncertainty.

## Failure handling

- Failed endpoint call: record error and lower confidence.
- Empty window: return explicit "no matching data in queried window".
- Pagination interrupted: label run as partial coverage.
