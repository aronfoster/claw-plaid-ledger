# Athena Query Guardrail Quick Reference

Use this sheet for deterministic analysis and reporting workflows.

## Global rules

- Default to `view=canonical`.
- Pair any `view=raw` call with an identical canonical query.
- Always set explicit `start_date` + `end_date`.
- Paginate `GET /transactions` deterministically.
- Label partial coverage when pagination/calls fail.

## Intent playbooks

### 1) Period spend summary

1. `GET /spend` with explicit date window + `view=canonical`.
2. `GET /transactions` with same window for evidence.
3. Separate posted vs pending in conclusions.

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

## Failure handling

- Failed endpoint call: report error and lower confidence.
- Empty window: return explicit "no matching data in queried window".
- Pagination interrupted: label findings as partial and avoid firm totals.
