# Sprint 8 — M7: Production operations and runbook

## Sprint goal

Prepare the project for first real-household production onboarding by shipping a practical,
operator-facing production runbook and a strict `ledger doctor --production-preflight`
check. Sprint 8 is complete when a developer can follow documented steps from sandbox to
production with clear safety rails, and the CLI can block obvious misconfiguration before
live credentials are used.

## Working agreements

- Keep each task reviewable in one PR where possible.
- Preserve backward compatibility for existing sandbox/local workflows.
- Do not automate Link/OAuth; this sprint is documentation + operator checks only.
- Run the quality gate before every commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Add or update tests for every behavior change.

## Task breakdown

### Task 1: Author production migration runbook (new `RUNBOOK.md`)

**Scope**

Create a committed runbook that operators can execute without reading implementation code.
This is the primary M7 deliverable.

**Required sections**

1. Purpose/scope and explicit non-goals for M7.
2. Plaid production-access prerequisites and dashboard checklist.
3. Cost model explanation:
   - clarify what events are billable,
   - clarify that sync frequency is not the primary billing lever,
   - include practical operator guidance to avoid accidental cost spikes.
4. Access-token lifecycle guidance:
   - secure persistence expectations,
   - revocation/relink scenarios,
   - forced re-auth edge cases and recovery actions.
5. Sandbox vs production isolation/safety checks:
   - required env separation,
   - DB/path separation,
   - preflight checks before first live sync.
6. Manual migration + first-live-sync validation checklist.
7. Backup/recovery guidance for SQLite and secrets/config files.
8. Incident appendix with quick triage steps (invalid token, webhook mismatch,
   stale cursor concerns, accidental wrong-environment config).

**Done when**

- A new operator can execute a dry run and first live sync using only `RUNBOOK.md`.
- The runbook contains copy-paste command snippets for each critical step.
- The document clearly distinguishes mandatory steps vs optional hardening.

---

### Task 2: Add `doctor --production-preflight` CLI option

**Scope**

Extend the `doctor` command with a production preflight mode that validates
live-readiness configuration without contacting external services.

**Behavior**

- New flag: `ledger doctor --production-preflight`
- Keep existing `ledger doctor` output unchanged when flag is not passed.
- In preflight mode, include pass/fail checks for:
  - required shared Plaid client vars,
  - API auth secret presence,
  - multi-item config presence/parseability (if using `items.toml`),
  - required access-token env var names referenced by `items.toml`,
  - DB path existence/creatability,
  - explicit warning when environment appears sandbox-like.
- Exit code:
  - `0` if all required checks pass,
  - non-zero if any required check fails.

**Done when**

- Missing required production config yields clear actionable errors.
- Passing preflight provides a concise success summary.
- Legacy `doctor` UX remains stable.

---

### Task 3: Implement production-preflight check layer in a testable module

**Scope**

Add a dedicated module (for example `preflight.py`) that encapsulates check
logic so CLI remains thin and behavior is unit-testable.

**Implementation notes**

- Model check results with typed structures (name, status, message, severity).
- Keep check functions pure where possible (input-driven, minimal side effects).
- Distinguish **hard failures** from **warnings**.
- Reuse existing config/items loader code rather than duplicating parsing logic.

**Done when**

- Core logic is tested via unit tests independent from Typer wiring.
- CLI test coverage verifies rendering + exit code behavior.

---

### Task 4: Add comprehensive tests for preflight and doctor integration

**Scope**

Expand test coverage to prevent regressions in safety-critical operator paths.

**Minimum tests**

- `doctor --production-preflight` success case with fully valid config.
- Failure cases for each required missing variable.
- `items.toml` parse error path (hard fail in preflight mode).
- Missing access-token env var referenced by an item (hard fail).
- Sandbox-like environment warning path.
- Legacy `doctor` invocation still exits 0 and preserves expected baseline output.

**Done when**

- Tests are deterministic and use `tmp_path` / monkeypatch for env isolation.
- No network calls are required in preflight tests.

---

### Task 5: Update operator documentation set

**Scope**

Align project docs with M7 so production operations expectations are discoverable.

**Required updates**

- `ARCHITECTURE.md`: add M7 operations/preflight section and reference `RUNBOOK.md`.
- `.env.example`: annotate production-sensitive variables and separation guidance.
- `README.md` (or equivalent entry doc): add a “Production preflight” usage snippet.

**Done when**

- A developer can find production setup and safety guidance from top-level docs.
- Docs do not contradict roadmap scope (manual Link/OAuth only, no automation).

---

### Task 6: Sprint closeout and acceptance validation

**Scope**

Validate M7 acceptance at sprint end and mark completion in this file.

**Checklist**

- Runbook committed and linked from main docs.
- `ledger doctor --production-preflight` implemented with tests.
- All quality gates green.
- Update this file by appending `✅ DONE` to each completed task heading.
- Add final “Sprint 8 closeout ✅ DONE” section summarizing what shipped and any
  explicitly deferred follow-ups.

---

## Acceptance criteria for Sprint 8

- A committed production runbook exists and covers all M7 scope bullets from `ROADMAP.md`.
- `ledger doctor --production-preflight` exists and enforces required live config checks.
- Preflight clearly separates blocking failures from warnings.
- Existing non-preflight workflows are not broken.
- Quality gate passes:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`

## Explicitly deferred (remain out of scope in Sprint 8)

- Automated Link/OAuth automation.
- Multi-item household ingestion workflow expansion (M8).
- Canonical overlap suppression and source precedence (M9).
- Multi-item webhook automation/routing changes (M10).
