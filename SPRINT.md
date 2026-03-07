# Sprint 2

## Sprint goal

Implement the first end-to-end Plaid ingestion path:

- connect to Plaid using configured credentials
- fetch transactions using a narrow initial sync path
- persist accounts, transactions, and sync cursor to SQLite
- make repeated syncs idempotent

This sprint is intentionally narrow. It does **not** include markdown exports,
OpenClaw notification, merchant rules, reconciliation logic beyond basic
pending/posting storage, or webhooks.

## Working agreements

- Keep changes small and independently reviewable.
- Prefer one standalone task per PR unless a dependency forces a pair.
- Preserve strict quality gates on every PR:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest`
- Use `Typer` as the CLI framework everywhere.
- Use standard-library `sqlite3` for the database layer.
- Keep secrets out of the OpenClaw workspace.
- SQLite remains the source of truth; no markdown projections in this sprint.
- Add appropriate unit and integration tests for each task. Use good judgment
  about exact test coverage, but do not skip tests for nontrivial behavior.

## Proposed task breakdown

### Task 1: Update architecture and planning docs for Sprint 2 reality

**Why**

The repo docs need to reflect the current state of the project before more
implementation lands. `ARCHITECTURE.md` still contains stale CLI framework
language and is due for a broader cleanup now that Sprint 1 is complete.

**Scope**

- Update `ARCHITECTURE.md` to describe `Typer` as the CLI framework
- Refresh any stale architecture notes that no longer match the codebase
- Ensure the documented interfaces, data flow, and runtime/tooling standards
  still match the intended Sprint 2 direction
- Update any other doc that is now obviously stale because Sprint 1 is done

**Done when**

- `ARCHITECTURE.md` no longer references `argparse` as the CLI framework
- Docs consistently describe the current project structure and standards
- Sprint 2 implementation can rely on the docs without tripping over stale
  guidance

**Testing expectation**

- Add or update any doc-adjacent tests only if the repo already has automation
  for them; otherwise focus testing effort on code changes in later tasks

---

### Task 2: Add Plaid client dependency and minimal client wrapper

**Why**

The repo now has config and a database bootstrap, but no integration layer for
Plaid. The sync engine needs a thin Plaid boundary instead of smearing API
calls across the CLI.

**Scope**

- Add the official Plaid Python client if not already present
- Create a small Plaid wrapper module
- Load Plaid client configuration from existing environment config
- Expose only the minimum operations needed for this sprint

**Done when**

- The app can construct a Plaid client from configured environment variables
- Plaid-specific setup is isolated in one module
- The CLI does not contain raw Plaid setup logic

**Testing expectation**

- Add appropriate unit tests for configuration and client construction behavior
- Mock external dependencies at the Plaid boundary; do not require live Plaid
  access for tests

**Notes**

- Keep the wrapper thin
- Do not add Link flow yet unless required for your chosen local bootstrap path
- Do not overabstract the Plaid SDK

---

### Task 3: Add sync-oriented domain model and DB write helpers

**Why**

The first sync needs a clean place to translate Plaid responses into your local
ledger schema.

**Scope**

- Add helpers for inserting/updating accounts
- Add helpers for inserting/updating transactions
- Add helpers for reading/writing `sync_state`
- Define a minimal normalization layer between Plaid response data and DB writes

**Done when**

- Accounts can be upserted by `plaid_account_id`
- Transactions can be upserted by `plaid_transaction_id`
- Sync cursor can be stored and re-read
- Repeat writes do not create duplicates

**Testing expectation**

- Add appropriate unit tests for normalization and DB helpers
- Add database-focused tests that verify inserts, updates, and reruns behave
  correctly

**Notes**

- Keep this deterministic
- Prefer explicit SQL over clever abstraction
- Do not add ORM machinery

---

### Task 4: Implement `ledger sync` CLI command skeleton

**Why**

The project’s interface list already assumes a `sync` entrypoint exists. The
next useful operator workflow is a real sync command.

**Scope**

- Add a `ledger sync` command
- Validate required Plaid-related config for sync
- Open the SQLite DB
- Call the sync engine
- Print a concise operator summary

**Done when**

- `uv run ledger sync --help` works
- `ledger sync` fails cleanly when Plaid config is missing
- `ledger sync` runs the sync path when config is present

**Testing expectation**

- Add appropriate CLI-focused unit or integration tests covering success and
  failure behavior
- Verify the command remains stable and operator-friendly

**Notes**

- Keep output operator-friendly and boring
- Do not add fancy progress UI
- A one-line summary is enough

---

### Task 5: Implement first transaction sync path

**Why**

This is the actual vertical slice: fetch data from Plaid and persist it locally.

**Scope**

- Implement one initial transaction sync flow
- Read prior cursor/state if present
- Fetch transaction changes from Plaid
- Persist accounts and transactions into SQLite
- Persist updated cursor/state

**Done when**

- A first sync writes accounts and transactions into SQLite
- A second sync without changes does not duplicate rows
- Sync state is persisted after a successful run

**Testing expectation**

- Add appropriate integration-style tests around the sync engine
- Mock Plaid responses at the boundary and verify persistence behavior end to
  end through the application modules involved
- Include tests that prove idempotency across repeated syncs

**Notes**

- Choose one Plaid transaction retrieval strategy and stick to it for this
  sprint
- Do not try to solve webhooks here
- Favor correctness and idempotency over coverage of every Plaid edge case

---

## Acceptance criteria for the sprint

- `ARCHITECTURE.md` is updated and no longer describes the CLI as `argparse`
- Plaid client can be constructed from configured environment
- `ledger sync` exists and runs through the sync engine
- First sync writes accounts, transactions, and cursor state to SQLite
- Repeated syncs are idempotent
- Appropriate unit and integration tests are added for nontrivial behavior
- Quality gate passes

## Explicitly deferred

- Plaid Link UX
- Markdown exports for OpenClaw
- OpenClaw wake-up / notification
- Merchant normalization rules
- Review queue generation
- Webhooks
- Budgeting or analytics views
