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

Before writing any annotations, load the current vocabulary:

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

1. `GET /transactions` with fixed window + `view=canonical`.
2. Paginate to completion.
3. Each row includes an `allocation` field (singular, list-view shape). Use
   `allocation.category`, `allocation.tags`, and `allocation.note` to screen
   for missing or stale categorization. The `allocation` object is always
   present (never null); fields within it may be null for uncategorized
   transactions. No per-record drill-down needed for initial screening.
   If the same transaction `id` appears in multiple list rows, it has been
   split — drill down with `GET /transactions/{id}` before any write.

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
5. Do **not** use `PUT /annotations/{transaction_id}` — it is a compatibility
   shim that returns HTTP 409 for split transactions.

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
