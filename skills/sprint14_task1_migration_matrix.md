# Sprint 14 Task 1 — Capability Inventory and Content Split Map

This matrix inventories Sprint 13 skill artifacts and assigns clear Sprint 14
ownership for the two-agent model.

## Migration matrix

| Current file/section | Future owner | Action | Rationale |
| --- | --- | --- | --- |
| `skills/hestia-ledger/SKILL.md` — Purpose + high-level role framing (summary + advisor language) | hestia | copy+adapt | Keep the API safety foundation but narrow the role to ingestion/annotation loop; remove advisor/reporting framing. |
| `skills/hestia-ledger/SKILL.md` — Boundaries/authority + canonical/raw guardrails | shared | copy+adapt | Deterministic API/query constraints apply to both agents and should become a shared guardrail contract. |
| `skills/hestia-ledger/SKILL.md` — Approved API calls list | shared | copy+adapt | Endpoint allowlist concept is reusable, but each agent needs a role-specific subset and ordering. |
| `skills/hestia-ledger/SKILL.md` — Required filter hygiene, pagination, failure behavior | shared | keep | Reusable operational hygiene; should be referenced by both bundles with minimal edits. |
| `skills/hestia-ledger/SKILL.md` — Playbook 1: period spend summary | athena | move | Human-facing rollup/reporting belongs to the analyst cadence, not ingestion worker runtime. |
| `skills/hestia-ledger/SKILL.md` — Playbook 2: owner-aware rollup | athena | move | Owner-level summaries are analyst output and create unnecessary token cost in Hestia loop. |
| `skills/hestia-ledger/SKILL.md` — Playbook 3: tag-based review | athena | copy+adapt | Athena should retain deep review; Hestia may keep only minimal tag checks needed for deterministic annotation quality. |
| `skills/hestia-ledger/SKILL.md` — Playbook 4: transaction drill-down before annotation write | hestia | keep | Core deterministic ingestion behavior before writing annotations. |
| `skills/hestia-ledger/SKILL.md` — Playbook 5: canonical vs raw discrepancy investigation | shared | copy+adapt | Diagnostic behavior is needed by both roles, but escalation destination differs (ops vs Athena queue). |
| `skills/hestia-ledger/SKILL.md` — Playbook 6: react to "Plaid sync complete" notification | hestia | keep | This is the primary Hestia wake path and should become the ingestion heartbeat. |
| `skills/hestia-ledger/SKILL.md` — Playbook 7: tag-combination spend review | athena | move | Spend interpretation by tag combination is analysis/reporting scope. |
| `skills/hestia-ledger/SKILL.md` — Annotation policy + required annotation shape | hestia | keep | Core bookkeeper write contract and safety rules remain with Hestia. |
| `skills/hestia-ledger/SKILL.md` — Response contract (facts/interpretation/follow-up) | shared | copy+adapt | Structure is useful for both, but Athena wording should be human-facing while Hestia output stays operational. |
| `skills/hestia-ledger/SKILL.md` — Owner-aware summary prompting rubric | athena | move | Explicit summary formatting is analyst-owned output behavior. |
| `skills/hestia-ledger/SKILL.md` — Anomaly taxonomy + anomaly workflow/confidence language | athena | copy+adapt | Athena owns anomaly interpretation and narratives; Hestia keeps only escalation tags such as `needs-athena-review`. |
| `skills/hestia-ledger/README.md` — "copy-ready skill bundle" install/copy steps | shared | keep | Bundle-install mechanics are identical for both agents and can be mirrored. |
| `skills/hestia-ledger/README.md` — endpoint/runtime assumptions | shared | copy+adapt | Shared baseline assumptions, with per-agent trigger/cadence differences added. |
| `skills/hestia-ledger/checklists/annotation_write_checklist.md` | hestia | keep | Directly aligned with deterministic annotation writes in ingestion loop. |
| `skills/hestia-ledger/checklists/query_playbooks.md` — Global rules + failure handling | shared | keep | Cross-agent query hygiene reference should remain reusable. |
| `skills/hestia-ledger/checklists/query_playbooks.md` — Intent playbooks 1/2/3/5 + anomaly quick flow | athena | move | These are analysis-heavy workflows and should be centered in Athena docs. |
| `skills/hestia-ledger/checklists/query_playbooks.md` — Drill-down before annotation write + orphaned-transaction triage | hestia | copy+adapt | Keep ingestion-safe drill-down and deterministic triage; retarget escalations to `needs-athena-review`. |
| `skills/hestia-ledger/templates/owner_summary_template.md` | athena | move | Pure human-facing reporting artifact. |
| `skills/hestia-ledger/templates/anomaly_review_template.md` | athena | move | Analyst-focused anomaly narrative and escalation template. |

## Salvage-first sections to port into Athena

Prioritize these sections for Task 3 so Athena reuses Sprint 13 analysis guidance
instead of rewriting from scratch:

1. `skills/hestia-ledger/SKILL.md` — **Playbook 1: period spend summary**.
2. `skills/hestia-ledger/SKILL.md` — **Playbook 2: owner-aware rollup**.
3. `skills/hestia-ledger/SKILL.md` — **Playbook 3: tag-based review**.
4. `skills/hestia-ledger/SKILL.md` — **Playbook 7: tag-combination spend review**.
5. `skills/hestia-ledger/SKILL.md` — **Owner-aware summary prompting rubric**.
6. `skills/hestia-ledger/SKILL.md` — **Anomaly taxonomy** and **handling/confidence language rules**.
7. `skills/hestia-ledger/checklists/query_playbooks.md` — analysis-oriented intent playbooks and anomaly quick flow.
8. `skills/hestia-ledger/templates/owner_summary_template.md`.
9. `skills/hestia-ledger/templates/anomaly_review_template.md`.

## Ownership resolution notes

- **Hestia** owns ingestion-triggered transaction review, deterministic annotation
  writes, and escalation tagging.
- **Athena** owns anomaly interpretation, spend/owner rollups, and human-facing
  summary narratives.
- **Shared** content is limited to API/query hygiene and reproducibility rules;
  any shared text should be imported/adapted with role-specific examples.

This removes ownership ambiguity for anomaly and summary workflows before Tasks
2–3 begin.
