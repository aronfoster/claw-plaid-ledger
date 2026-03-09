# Sprint 4 — M3: Agent-friendly exports

## Sprint goal

Write markdown summaries and inbox files into the OpenClaw workspace so that
LLM agents can act on recent transaction data without querying SQLite directly:

- Export recent transactions as structured markdown into the OpenClaw workspace
- Write a sync-inbox file that signals new or changed transactions to an agent
- Make exports idempotent and safe to regenerate on every sync run
- Document the export format so agents can rely on stable field names

This sprint is intentionally focused on the agent-readable export layer.
It does not include merchant normalization, review queues, or notification
triggering.

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
- SQLite remains the source of truth; markdown exports are derived views.
- Add appropriate unit and integration tests for each task. Use good judgment
  about exact test coverage, but do not skip tests for nontrivial behavior.

## Proposed task breakdown

### Task 1: Export recent transactions as markdown

**Why**

Agents reading the OpenClaw workspace need a stable, structured snapshot of
recent transactions without parsing SQLite or running CLI commands.

**Scope**

- Add an `export` subcommand to the CLI
- Write a markdown file (`transactions.md`) into `CLAW_PLAID_LEDGER_WORKSPACE_PATH`
  containing the most recent N transactions (default: 90 days or 500 rows)
- Each row: date, merchant name, amount, category, account, transaction ID
- File is overwritten on every export (idempotent)
- Exit non-zero and report an error if `CLAW_PLAID_LEDGER_WORKSPACE_PATH` is
  not set

**Done when**

- `ledger export` writes `transactions.md` to the workspace path
- File is well-formed markdown with a header row and one row per transaction
- Command exits non-zero when the workspace path is not configured
- Command exits non-zero when the DB is not reachable

**Testing expectation**

- Add tests for: successful export, missing workspace path, missing DB

---

### Task 2: Write a sync-inbox file

**Why**

An agent polling the workspace needs a lightweight signal file that lists
only the transaction IDs added or modified in the most recent sync, so it
does not need to diff the full export on every wake-up.

**Scope**

- After a successful sync, write `sync-inbox.md` into the workspace path
- File lists: sync timestamp, counts (added / modified / removed), and the
  transaction IDs of added and modified transactions
- File is overwritten on every sync (idempotent); an empty run still
  overwrites with zero counts
- Skip writing if `CLAW_PLAID_LEDGER_WORKSPACE_PATH` is not configured
  (log a debug message but do not error)

**Done when**

- `ledger sync` writes `sync-inbox.md` when workspace path is configured
- File format is documented in `ARCHITECTURE.md`
- Workspace path not set is a silent no-op, not an error

**Testing expectation**

- Add tests for: inbox written on successful sync, inbox not written when
  workspace path absent, inbox reflects correct counts

---

### Task 3: Document the export format

**Why**

Agents consuming exports must be able to rely on stable field names and
file layout. Documentation ensures that future changes to the export format
are deliberate and versioned.

**Scope**

- Add an "Export format" section to `ARCHITECTURE.md` describing:
  - `transactions.md` schema (columns, types, date format)
  - `sync-inbox.md` schema (fields, timestamp format)
  - Overwrite semantics (idempotent regeneration)
- Update `README.md` to mention the `export` command and workspace path
  requirement

**Done when**

- `ARCHITECTURE.md` fully describes the export format
- `README.md` mentions `ledger export` in the quick-start section

**Testing expectation**

- Documentation changes only; no new tests required

---

## Acceptance criteria for the sprint

- `ledger export` writes a well-formed `transactions.md` to the workspace
- `ledger sync` writes `sync-inbox.md` to the workspace when configured
- Export format is documented in `ARCHITECTURE.md`
- Quality gate passes on all PRs
- All documentation reflects post-sprint reality

## Explicitly deferred

- OpenClaw wake-up / notification (M4)
- Merchant normalization rules (M5)
- Review queue generation (M5)
- Webhooks
- Budgeting or analytics views
- Plaid Link UX
