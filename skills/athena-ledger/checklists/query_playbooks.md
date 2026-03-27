# Athena Query Guardrail Quick Reference

Use this sheet for deterministic analysis and reporting workflows.

## Global rules

- Default to `view=canonical`.
- Pair any `view=raw` call with an identical canonical query.
- Always set explicit `start_date` + `end_date`, or use a `range` shorthand.
  Both `GET /spend` and `GET /transactions` accept `range`.
- `GET /transactions` list results include nested `allocation` data (singular
  object — list-view shape). The `allocation` object is always present (never
  null); `category`, `tags`, and `note` within it may be null for uncategorized
  transactions. Use `allocation.category`, `allocation.tags`, and
  `allocation.note` for initial triage; drill into `GET /transactions/{id}`
  only when quoting amounts or allocation details in a final report.
  `GET /transactions/{id}` returns `"allocations": [...]` (array); use
  `allocations[0]` for unsplit transactions or iterate all elements for split ones.
- For split transactions: the same transaction `id` appears once per allocation
  in list results. Grouping by `id` reveals split transactions and their
  per-category breakdown.
- Paginate `GET /transactions` deterministically.
- Label partial coverage when pagination/calls fail.

## Pagination mechanics

Response shape for `GET /transactions`:
`{ "transactions": [...], "total": N, "limit": L, "offset": O }`

- Advance: `offset += limit` after each page.
- Stop: when `offset >= total`.
- Keep `limit` stable within a run (recommended: `100`).
- If interrupted: report partial coverage; do not make totals claims.

## Vocabulary discovery

Before annotating, retrieve the current allocation vocabulary:

- `GET /categories` — returns sorted list of distinct allocation category values.
- `GET /tags` — returns sorted flat list of distinct allocation tag values.

Use these to avoid creating near-duplicate labels across runs.

## Intent playbooks

### 1) Period spend summary

Option A — explicit window:

1. `GET /spend` with `start_date`, `end_date`, `view=canonical`.
2. `GET /transactions` with same window for evidence.
3. Separate posted vs pending in conclusions.

Option B — range shorthand (interactive / quick queries):

1. `GET /spend?range=last_month` (or `this_month`, `last_30_days`,
   `last_7_days`).
2. Confirm the resolved `start_date`/`end_date` in the response.
3. Run `GET /transactions?range=last_month` (same shorthand accepted) for
   evidence. List results include `allocation` — no drill-down needed for
   triage.

Optional: add `account_id`, `category`, or `tag` to narrow the aggregation.

### 2) Owner-aware rollup

1. `GET /spend` with `owner`, date window, `view=canonical`.
2. `GET /transactions` with matching owner/date filters.
3. `GET /transactions/{id}` for outliers before quoting details.

### 3) Tag-based review

1. `GET /transactions` with `tags` + explicit date window.
2. Paginate to completion before trend claims.
3. If sparse/empty, report insufficient tagged evidence.

### 4) Needs-Athena queue review

1. `GET /transactions` with `tags=needs-athena-review` + explicit window.
2. `GET /transactions/{id}` for each priority candidate.
3. Group findings by anomaly/review type and recommend follow-up actions.

### 5) Canonical vs raw discrepancy check

1. `GET /transactions` with canonical view over fixed filters.
2. Repeat exactly with raw view.
3. Report differences as investigation guidance, not override instructions.

### 6) Account-scoped spend

1. `GET /accounts` to list all known accounts with labels.
2. Identify the target `account_id` from the response.
3. `GET /spend` with `account_id=<id>` and desired date window.
4. Optionally narrow further with `category` or `tag` filters.

### 7) Month-over-month trends

1. `GET /spend/trends` with `?months=<n>` (default 6).
2. Note which buckets have `partial: true` (current month) — exclude from
   comparisons or call out explicitly.
3. Each bucket contains `allocation_count` (not `transaction_count`) — this
   reflects allocation rows. For split transactions, one transaction contributes
   multiple allocation rows.
4. To narrow the trend to a subset, add the same filters used in
   `GET /spend`: `owner`, `account_id`, `category`, `tag`.
5. To validate a specific month's total, cross-check with
   `GET /spend?start_date=<YYYY-MM-01>&end_date=<YYYY-MM-last-day>`
   using matching filters — the numbers must agree.

### 8) Reviewing split transactions

1. In `GET /transactions` list results, group rows by transaction `id`. Any
   `id` that appears more than once is a split transaction; each row carries
   one allocation's amount, category, and tags.
2. Drill into `GET /transactions/{id}` to see all allocations together in the
   `"allocations": [...]` array. Split transactions have `allocations.length > 1`.
3. For spend rollups filtered by category: `GET /spend?category=groceries`
   correctly sums only the grocery allocation amounts — not full transaction
   amounts — so per-category totals are accurate even for split transactions.
4. Do not overwrite an operator-defined split unless explicitly instructed.
   Flag unusual splits for operator review.

### 9) Ledger health check

1. `GET /errors?hours=24` — retrieve warnings and errors from the last 24h.
2. If `total > 0`, group rows by `severity` and `logger_name`.
3. For ERROR-level rows: include in any summary report with the `message` and
   `correlation_id` for operator follow-up.
4. For WARNING-level rows: note the count; escalate only if they form a
   repeating pattern or accompany anomalous transaction data.
5. To broaden the window: `?hours=168` (last 7 days).
6. To narrow to errors only: add `?min_severity=ERROR`.

## Failure handling

- Failed endpoint call: report error and lower confidence.
- Empty window: return explicit "no matching data in queried window".
- Pagination interrupted: label findings as partial and avoid firm totals.
