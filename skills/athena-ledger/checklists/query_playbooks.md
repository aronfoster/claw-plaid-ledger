# Athena Query Guardrail Quick Reference

Use this sheet for deterministic analysis and reporting workflows.

## Global rules

- Default to `view=canonical`.
- Pair any `view=raw` call with an identical canonical query.
- Always set explicit `start_date` + `end_date`, or use a `range` shorthand.
- Paginate `GET /transactions` deterministically.
- Label partial coverage when pagination/calls fail.

## Vocabulary discovery

Before annotating, retrieve the current vocabulary:

- `GET /categories` — returns sorted list of distinct category values.
- `GET /tags` — returns sorted flat list of distinct tag values.

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
3. Run `GET /transactions` with matching window for evidence.

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
3. To narrow the trend to a subset, add the same filters used in
   `GET /spend`: `owner`, `account_id`, `category`, `tag`.
4. To validate a specific month's total, cross-check with
   `GET /spend?start_date=<YYYY-MM-01>&end_date=<YYYY-MM-last-day>`
   using matching filters — the numbers must agree.

## Failure handling

- Failed endpoint call: report error and lower confidence.
- Empty window: return explicit "no matching data in queried window".
- Pagination interrupted: label findings as partial and avoid firm totals.
