# Query Guardrail Quick Reference

Use this sheet to keep endpoint usage deterministic and safe.

## Global rules

- Default to `view=canonical`.
- Treat `view=raw` as diagnostic-only and pair with canonical in the same
  window.
- Always set `start_date` + `end_date` for summary/review workflows.
- Use deterministic pagination for `GET /transactions` and disclose partial
  coverage if paging fails.
- Never recommend canonical precedence overrides.

## Intent playbooks

### 1) Period spend summary

1. `GET /spend` with explicit date window + `view=canonical`.
2. `GET /transactions` with the same window to collect representative records.
3. Separate posted vs pending observations before conclusions.

### 2) Owner-aware rollup

1. `GET /spend` with `owner`, date window, `view=canonical`.
2. `GET /transactions` with matching owner/date filters.
3. `GET /transactions/{id}` for any outlier before quoting details.

### 3) Tag-based review

1. `GET /transactions` with `tags` + explicit date window.
2. Paginate to completion before asserting totals or trends.
3. If empty/sparse, report insufficient tagged evidence.

### 4) Drill-down before annotation write

1. `GET /transactions/{id}` to verify record identity and current annotation.
2. Optional filtered `GET /transactions` to resolve context conflicts.
3. `PUT /annotations/{transaction_id}` only if evidence is sufficient.

### 5) Canonical vs raw discrepancy check

1. `GET /transactions` with canonical view over fixed filters.
2. Repeat exactly with raw view.
3. Report differences as operator follow-up, not override guidance.

## Failure handling

- Failed endpoint call: report error and reduce confidence.
- Empty window across relevant calls: return explicit "no matching data in
  queried window".
- Pagination interrupted: label findings as partial and avoid definitive totals.

## Anomaly-review quick flow

1. Run canonical queries over a fixed window and complete pagination.
2. Classify findings as one or more of: `spend-spike`, `missing-expected`,
   `possible-duplicate`, `category-mismatch`.
3. Re-fetch each candidate with `GET /transactions/{id}` before any annotation.
4. If confidence is low or evidence is partial, respond with
   "needs human review" and abstain from definitive conclusions.
5. If annotating, use `review-needed` plus a specific anomaly tag and include
   timeframe + follow-up action in the note.
