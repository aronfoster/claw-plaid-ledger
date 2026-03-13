# Sprint 14 — M12a Redirect: Two-Agent Ledger Workflow

## Sprint goal

Redirect the completed M12 work into a practical **two-agent operating model**:

- **Hestia (Bookkeeper):** high-frequency ingestion worker that annotates new
  transactions deterministically.
- **Athena (Analyst):** lower-frequency analysis worker that reviews flagged
  items, produces rollups, and communicates insights.

By the end of Sprint 14, the repository should ship two copy-ready skill
bundles and clear runtime/documentation boundaries so operators can run Hestia
continuously without creating human-notification fatigue.

## Why this sprint exists

Sprint 13 successfully produced a strong single-agent skill pack, but customer
feedback exposed a role-mixing problem: annotation throughput work and
higher-order analysis/reporting were bundled into one agent contract.

Sprint 14 reuses that work rather than replacing it:

- Keep deterministic API guardrails and annotation hygiene foundations.
- Move analysis/reporting/anomaly synthesis content into Athena.
- Narrow Hestia to an operationally cheap, deterministic ingestion loop with
  explicit escalation tags.

## Working agreements

- Keep each task reviewable in one PR where possible.
- Prefer refactoring/re-homing existing content over rewriting from scratch.
- Preserve privacy-safe placeholders and deterministic API guidance.
- Do not add new API endpoints unless unavoidable; optimize for doc + skill
  contract changes first.
- Run the quality gate before every commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`

---

## Task 1: Capability inventory and content split map

### Scope

Audit current Sprint 13 artifacts (skill files/templates/checklists/docs) and
produce a concrete migration map: what stays with Hestia, what moves to Athena,
and what should be shared.

### Required deliverables

- A checked-in migration matrix (markdown) with columns:
  - current file/section,
  - future owner (`hestia`, `athena`, `shared`),
  - action (`keep`, `move`, `copy+adapt`, `retire`),
  - rationale.
- Explicit list of “salvage-first” sections to port into Athena.

### Done when

- Every major Sprint 13 artifact has an explicit destination.
- No ambiguous ownership remains for anomaly/summary workflows.
- The map is specific enough that a developer can execute Tasks 2–3 without
  interpretation meetings.

---

## Task 2: Refactor Hestia skill bundle to ingestion-only contract

### Scope

Narrow `skills/hestia-ledger/` to a strict bookkeeper loop and remove analyst
responsibilities.

### Required deliverables

- Updated Hestia `SKILL.md` boundaries:
  - Fetch unannotated transactions.
  - Perform deterministic categorization/tagging.
  - Write annotation updates.
  - Escalate uncertain cases via `needs-athena-review` tag and continue.
- Updated Hestia templates/checklists to support only:
  - annotation quality,
  - confidence thresholds,
  - escalation tagging.
- Removal or relocation of reporting/anomaly-summary language that implies
  Hestia speaks directly to end users.

### Done when

- Hestia docs no longer claim spend-summary/reporting ownership.
- Hestia can be run frequently with low token/cost expectations.
- Escalation behavior is explicit, deterministic, and machine-checkable.

---

## Task 3: Create Athena skill bundle by salvaging Sprint 13 analysis work

### Scope

Create `skills/athena-ledger/` by reusing and adapting existing Sprint 13
analysis-focused guidance.

### Required deliverables

- New Athena `SKILL.md` with analyst boundaries:
  - Review `needs-athena-review` queue.
  - Run `GET /spend` rollups for defined windows.
  - Produce owner-aware summaries and anomaly narratives.
  - Avoid high-volume annotation churn except analyst-level clarifications.
- Athena companion templates/checklists migrated from Sprint 13 artifacts:
  - summary template,
  - anomaly review flow,
  - weekly/monthly playbook.
- Athena README describing trigger modes:
  - scheduled cadence,
  - optional agent-to-agent handoff when available.

### Done when

- Athena bundle is copy-ready and self-contained.
- At least 70% of analysis guidance is reused from Sprint 13 assets (adapted,
  not duplicated blindly).
- Athena output contract is clearly “human-facing analysis,” not ingestion.

---

## Task 4: Notification + architecture alignment for two-agent routing

### Scope

Align runtime defaults and architecture docs with Hestia-first ingestion and
Athena-second analysis.

### Required deliverables

- `notifier.py`/config defaults and docs updated so the webhook wake target is
  explicitly Hestia as ingestion worker.
- Notification message copy updated to remove language implying immediate human
  review on every sync.
- `ARCHITECTURE.md` updated with two-agent sequence:
  1. Plaid sync event,
  2. Hestia annotation pass,
  3. Athena scheduled/flag-driven analysis.

### Done when

- Runtime and docs are non-contradictory about who is woken first.
- Operator can answer “who does what, and when?” from architecture docs alone.
- Legacy single-agent wording is removed or clearly marked as historical.

---

## Task 5: Sprint closeout validation and operator handoff docs

### Scope

Ensure the redirected model is runnable by downstream developers/operators and
that sprint tracking reflects completion quality.

### Required deliverables

- Closeout section in `SPRINT.md` summarizing shipped artifacts and any deferred
  follow-ups.
- Quickstart snippets for:
  - installing/copying Hestia bundle,
  - installing/copying Athena bundle,
  - recommended schedule (Hestia event-driven, Athena periodic).
- Evidence that quality gate passed on final integration PR.

### Done when

- New developer can bootstrap both skills without tribal knowledge.
- Deferred items are explicit (not implied).
- Sprint board is ready for handoff to Sprint 15 planning.

---

## Acceptance criteria for Sprint 14

- Two distinct, reusable skill bundles exist (`hestia-ledger`,
  `athena-ledger`) with non-overlapping primary responsibilities.
- Hestia contract is strictly ingestion + annotation + escalation tagging.
- Athena contract covers rollups, anomaly interpretation, and human-facing
  summaries.
- Notification and architecture docs reflect Hestia-first wake flow.
- All quality-gate commands pass on merged implementation PRs.

## Explicitly deferred (out of scope for Sprint 14)

- New ledger API endpoints dedicated to Athena unless blocked by a hard gap.
- Full orchestration engine for guaranteed agent-to-agent execution.
- M13 deployment hardening and M14 doctor auto-remediation roadmap items.
