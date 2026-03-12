# Sprint 13 — M12: Hestia Skill Definition

## Sprint goal

Publish a production-ready Hestia skill pack that defines **how Hestia should
use the ledger API safely and deterministically**. By the end of the sprint,
a developer can copy the skill files into a fresh OpenClaw workspace and get
consistent behavior for household summaries, anomaly detection, and annotation
hygiene without overriding canonical source-precedence rules.

## Working agreements

- Keep each task reviewable in one PR where possible.
- Prefer additive documentation and examples over speculative code changes.
- Keep the skill contract implementation-agnostic for OpenClaw runtime details,
  but concrete about API behavior and decision boundaries.
- Do not weaken existing architecture boundaries: Hestia is an advisor and
  annotator, never a raw-ledger mutator.
- Run the quality gate before every commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`

---

## Task 1: Create the Hestia skill file bundle

### Scope

Create a dedicated, copy-friendly skill bundle for OpenClaw containing a
complete `SKILL.md` and any companion markdown/templates needed for reuse.
The bundle should live in a clearly named docs/location path and be structured
so operators can copy it as-is into a new Hestia/OpenClaw skill project.

### Required deliverables

- New `SKILL.md` authored for Hestia ledger operations.
- A short `README` (or section in existing docs) explaining:
  - where files live,
  - what to copy,
  - minimum required environment assumptions.
- Optional companion artifacts (prompt templates, checklists) kept as separate
  files rather than embedded giant blocks in one file.

### SKILL.md must include

- **Purpose and boundaries** (what Hestia is and is not allowed to do).
- **Approved tool/API calls** and expected call order for common workflows.
- **Determinism rules** (prefer canonical views, date-window constraints,
  explicit filters, and reproducible reasoning steps).
- **Annotation policy** (when to write annotations, required fields/format,
  and when to abstain).

### Done when

- A developer can copy the bundle into a new OpenClaw skill project without
  editing internal links.
- The file layout is obvious and documented.
- The skill reads as operational guidance, not aspirational prose.
- Quality gate passes.

---

## Task 2: Define API guardrails and deterministic query playbooks

### Scope

Codify exactly how Hestia should query the existing API endpoints (`/transactions`,
`/transactions/{id}`, `/spend`, `/annotations/{transaction_id}`) to produce
consistent results and avoid over-fetching or nondeterministic summaries.

### Required content

- Canonical-first guidance (`view=canonical` default) and explicit carve-outs
  for when `view=raw` is allowed.
- Query playbooks for common intents:
  - period spend summaries,
  - owner-aware rollups,
  - tag-based review,
  - transaction drill-down before annotation writes.
- Required filter hygiene:
  - always specify date windows for summary tasks,
  - use pagination patterns explicitly,
  - avoid ambiguous keyword-only conclusions.
- Error-handling guidance for partial/empty result sets and API failures.

### Done when

- At least 4 concrete “intent → API call sequence” playbooks are documented.
- Guardrails explicitly prevent precedence override behavior.
- Guidance is aligned with current API capabilities shipped through M11.
- Quality gate passes.

---

## Task 3: Add owner-aware summary and anomaly-review prompting guidance

### Scope

Add practical prompting patterns that make Hestia produce high-signal household
summaries while clearly separating facts, uncertainty, and suggested follow-up.
Include anomaly workflows that treat Hestia as a safety net.

### Required content

- Prompting rubric for owner-aware output:
  - per-owner sections,
  - shared-household rollup,
  - pending-vs-posted clarity,
  - citation of queried timeframe.
- Anomaly taxonomy and handling steps for:
  - unusual spend spikes,
  - missing expected transactions,
  - likely duplicates,
  - category/tag inconsistencies.
- Confidence language rules:
  - how to mark “needs human review”,
  - when to abstain from definitive conclusions.

### Done when

- Prompts/templates produce structured output (not free-form only).
- Anomaly workflow includes escalation path and recommended annotation pattern.
- Guidance explicitly states Hestia cannot rewrite canonical precedence decisions.
- Quality gate passes.

---

## Task 4: Document orphaned-transaction and discrepancy workflows

### Scope

Define explicit safety-net workflows for “orphaned transactions” and
cross-source discrepancies, including what Hestia should check, how it should
annotate findings, and where human/operator intervention is required.

### Required content

- Clear definition of “orphaned transaction” in this project context.
- Step-by-step triage flow:
  1. detect,
  2. validate with available API data,
  3. annotate with standardized tags/notes,
  4. recommend operator follow-up action.
- A discrepancy decision table that distinguishes:
  - data freshness/sync timing issues,
  - true cross-account conflicts,
  - likely annotation drift.

### Done when

- Workflow can be followed mechanically by a new developer/operator.
- Annotation examples are generic and privacy-safe.
- The process frames Hestia as detection/reporting support only.
- Quality gate passes.

---

## Task 5: Architecture alignment and sprint closeout

### Scope

Update architecture docs to reflect the finalized agent-role boundary and verify
that roadmap/sprint tracking reflects M12 planning status for downstream work.

### Checklist

- `ARCHITECTURE.md` updated with an “Agent role boundary” section covering:
  - canonical ledger authority,
  - Hestia read/annotate responsibilities,
  - prohibited override behaviors.
- Any relevant operator docs cross-link to the new skill bundle location.
- `ROADMAP.md` remains consistent with M12 scope language.
- Add a Sprint 13 closeout section after completion summarizing shipped assets
  and explicitly deferred follow-ups.

### Done when

- Architecture and skill guidance are non-contradictory.
- A developer can answer “what is Hestia allowed to do?” from docs alone.
- Sprint board is ready for closeout once tasks complete.
- Quality gate passes.

---

## Acceptance criteria for Sprint 13

- Hestia skill files exist as reusable, copy-friendly artifacts.
- `SKILL.md` defines deterministic API usage and annotation hygiene.
- Owner-aware summary and anomaly-review prompting guidance is documented with
  structured templates/playbooks.
- Orphaned/discrepancy workflows are explicitly documented with clear human
  escalation boundaries.
- Architecture docs clearly enforce Hestia as a safety net, not precedence
  authority.
- All quality-gate commands pass.

## Explicitly deferred (out of scope for Sprint 13)

- New API endpoints or database schema changes for M12.
- Deployment hardening work (M13).
- Doctor auto-remediation work (M14).
