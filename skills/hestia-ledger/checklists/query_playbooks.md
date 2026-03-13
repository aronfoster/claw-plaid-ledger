# Hestia Ingestion Query Playbooks

Use this sheet for deterministic ingestion and escalation-only behavior.

## Global rules

- Default to `view=canonical`.
- Use `view=raw` only for discrepancy diagnostics over identical filters.
- Always set explicit `start_date` + `end_date`.
- Use deterministic pagination for `GET /transactions`.
- Never recommend canonical precedence overrides.

## Ingestion playbooks

### 1) Sync intake and candidate detection

1. `GET /transactions` with fixed window + `view=canonical`.
2. Paginate to completion.
3. Queue records missing expected annotation context or showing uncertainty.

### 2) Drill-down before write

1. `GET /transactions/{id}` to verify current details.
2. Optional filtered `GET /transactions` to resolve conflicting context.
3. `PUT /annotations/{transaction_id}` only if evidence is sufficient.

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
