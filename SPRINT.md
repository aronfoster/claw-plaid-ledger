# Sprint 3 — M2: Local Ledger Hardening

## Sprint goal

Harden the local ledger for reliable day-to-day operation:

- Fix known bugs identified in Sprint 2
- Adopt real Typer as the CLI framework
- Replace the `doctor` placeholder with real operational diagnostics
- Make `item_id` configurable for multi-institution households
- Improve sync atomicity and error resilience
- Close out M2 documentation

This sprint is intentionally focused on correctness and operability.
It does not include markdown exports, OpenClaw notification, merchant
normalization, or encryption at rest.

## Working agreements

- Keep changes small and independently reviewable.
- Prefer one standalone task per PR unless a dependency forces a pair.
- Preserve strict quality gates on every PR:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest`
- Use `Typer` (real library) as the CLI framework everywhere.
- Use standard-library `sqlite3` for the database layer.
- Keep secrets out of the OpenClaw workspace.
- SQLite remains the source of truth; no markdown projections in this sprint.
- Add appropriate unit and integration tests for each task. Use good judgment
  about exact test coverage, but do not skip tests for nontrivial behavior.

## Proposed task breakdown

### Task 1: Fix BUG-001 — account_count inflation in SyncSummary

**Why**

The sync loop accumulates `account_count` on every page, so the
operator-facing summary reports accounts × pages rather than distinct
accounts. No data integrity impact, but the output is misleading.

**Scope**

- Fix accumulation in `sync_engine.py` using the set-based approach
  documented in `BUGS.md`
- Add a regression test covering multi-page pagination that asserts the
  reported account count equals distinct accounts, not pages × accounts
- Mark BUG-001 resolved in `BUGS.md`

**Done when**

- `SyncSummary.accounts` reflects distinct account count regardless of page
  count
- Regression test passes
- BUG-001 marked resolved

**Testing expectation**

- Add a test that configures a `FakeAdapter` with `has_more=True` on the
  first page and asserts the final summary reports the correct account count

---

### Task 2: Fix BUG-002 — adopt real Typer

**Why**

`src/typer.py` is a hand-rolled shim that shadows the real `typer` package.
`ARCHITECTURE.md` documents Typer as the CLI framework. Any LLM agent or
contributor reading the docs will write real Typer syntax that silently
breaks against the shim. This must be resolved before any further CLI work.

**Scope**

- Add `typer` to `pyproject.toml` dependencies
- Delete `src/typer.py`
- Update `cli.py` to use real Typer imports and conventions
- Verify all existing CLI tests pass without modification
- Update `ARCHITECTURE.md` to confirm real Typer is now in use
- Mark BUG-002 resolved in `BUGS.md`

**Done when**

- `src/typer.py` is deleted
- `typer` appears in `pyproject.toml`
- All existing tests pass
- BUG-002 marked resolved

**Testing expectation**

- No new tests required if existing CLI tests pass unchanged; the test suite
  is the regression signal here

**Notes**

- Land this before any other task that touches CLI surface area

---

### Task 3: Harden the `doctor` command

**Why**

The `doctor` command unconditionally prints "basic checks passed" — it is
a placeholder. Operational confidence requires real diagnostics before an
operator attempts a sync.

**Scope**

- Validate required env vars are present and non-empty
- Confirm the DB file exists and is reachable via `sqlite3`
- Verify schema integrity (all three expected tables present)
- Report last sync time and row counts from `sync_state`, `accounts`,
  `transactions`
- With `--verbose`, show full config values with secrets redacted to last
  4 characters

**Done when**

- `doctor` exits non-zero and reports specific failures when config or DB
  is missing or malformed
- `doctor` exits zero and reports row counts and last sync time when
  everything is healthy
- `doctor --verbose` shows redacted config values

**Testing expectation**

- Add tests for missing config, missing DB file, healthy state, and
  verbose output
- Verify secrets are redacted in verbose output

---

### Task 4: Make item_id configurable

**Why**

`DEFAULT_ITEM_ID = "default-item"` is hardcoded in `sync_engine.py`. This
is a silent correctness trap for any household connecting more than one
institution.

**Scope**

- Add `CLAW_PLAID_LEDGER_ITEM_ID` as an optional env var in `config.py`,
  defaulting to `"default-item"` for backward compatibility
- Thread the value through to the `sync` command and `run_sync`
- Document the new var in `.env.example` and `README.md`

**Done when**

- `CLAW_PLAID_LEDGER_ITEM_ID` is respected when set
- Default behavior is unchanged when the var is absent
- `.env.example` and `README.md` document the new key

**Testing expectation**

- Add a test that sets the env var and verifies the correct `item_id` is
  used in sync state reads and writes

---

### Task 5: Sync atomicity and error resilience

**Why**

A Plaid exception mid-loop (network blip, rate limit, malformed response)
leaves the cursor un-advanced, so the next run restarts from scratch. This
is safe but expensive for large initial syncs and opaque to the operator.

**Scope**

- Add explicit error handling in the sync loop that distinguishes transient
  from permanent Plaid errors
- Add a comment making the cursor-write-after-success invariant explicit
- Add a test that simulates a mid-loop exception and verifies no partial
  state is committed and the prior cursor is preserved

**Done when**

- Transient errors surface a clear operator message without corrupting state
- The cursor-after-success invariant is tested and commented
- Prior cursor is preserved on exception

**Testing expectation**

- Add a `FakeAdapter` variant that raises on a configured page number and
  verify DB state and cursor after the failed run

---

### Task 6: Update and close out M2 documentation

**Why**

Docs should reflect the current state of the codebase after each sprint so
that LLM agents and contributors do not work from stale guidance.

**Scope**

- Replace `SPRINT.md` with the M3 sprint plan
- Update `ROADMAP.md` to mark M2 complete
- Update `ARCHITECTURE.md` to reflect real Typer (after Task 2) and
  configurable `item_id` (after Task 4)
- Update `README.md` and other markdown files with any information that has
  become stale or is missing after this sprint's changes
- Confirm `BUGS.md` reflects resolved status for BUG-001 and BUG-002

**Done when**

- No markdown file describes the project in terms that contradict the
  post-sprint codebase
- `ROADMAP.md` marks M2 complete

**Testing expectation**

- Documentation changes only; no new tests required

---

## Acceptance criteria for the sprint

- BUG-001 and BUG-002 are resolved and marked closed in `BUGS.md`
- Real `typer` library is the CLI framework; `src/typer.py` shim is deleted
- `doctor` provides real operational diagnostics including DB health and
  row counts
- `CLAW_PLAID_LEDGER_ITEM_ID` is configurable and documented
- Sync loop handles mid-loop exceptions without corrupting cursor state
- Quality gate passes on all PRs
- All documentation reflects post-sprint reality

## Explicitly deferred

- Markdown exports for OpenClaw
- OpenClaw wake-up / notification
- Merchant normalization rules
- Review queue generation
- Webhooks
- Budgeting or analytics views
- Plaid Link UX
