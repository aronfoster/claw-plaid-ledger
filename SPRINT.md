# Sprint 1

## Sprint goal

Replace the current CLI implementation with `Typer`, then add the
minimum configuration and database bootstrap needed to support the
first Plaid sync.

This sprint is intentionally narrow. It does **not** include real Plaid
API integration, markdown exports, OpenClaw notification, reconciliation,
merchant rules, or webhooks.

## Working agreements

- Keep changes small and independently reviewable.
- Prefer one standalone task per PR unless a dependency forces two
  tightly related changes together.
- Preserve strict quality gates on every PR:
  - `uv run ruff format . --check`
  - `uv run ruff check .`
  - `uv run mypy`
  - `uv run pytest`
- Use `Typer` as the CLI framework everywhere.
- Use standard-library `sqlite3` for the database layer.
- Keep secrets out of the OpenClaw workspace.

## Proposed task breakdown

### Task 1: Replace `argparse` CLI with `Typer`

**Why**

The project standard is `Typer`, but the repo currently still describes
or uses `argparse` in some places. The CLI framework needs to be made
consistent before more commands are added.

**Scope**

- Replace the current `argparse`-based CLI entrypoint with `Typer`
- Preserve the existing `doctor` command behavior
- Keep the `ledger` command-line entrypoint stable
- Add or update CLI tests to cover the Typer-based command

**Done when**

- `uv run ledger --help` works through `Typer`
- `uv run ledger doctor` works
- Existing or updated CLI tests pass
- No remaining source code uses `argparse` for the app CLI

**Notes**

- Keep the CLI small and boring
- Do not add subcommand groups unless they are clearly needed

---

### Task 2: Update docs to consistently describe `Typer`

**Why**

The docs currently disagree about the chosen CLI framework. That is a
fine way to confuse both humans and coding models.

**Scope**

- Update `README.md`
- Update `ARCHITECTURE.md`
- Update any other doc that still references `argparse`
- Ensure the CLI framework is described as `Typer` everywhere

**Done when**

- Docs consistently describe `Typer` as the CLI framework
- Quick-start commands still match the real CLI
- No stale `argparse` references remain in repo docs unless they are
  historical notes

---

### Task 3: Add configuration loading from environment

**Why**

The sync and database code need one place to obtain runtime settings
cleanly and predictably.

**Scope**

- Add a config module that reads settings from environment variables
- Validate required settings
- Support a configurable database path
- Support a configurable OpenClaw workspace path if present
- Support Plaid credentials/settings as environment-based inputs, but
  only validate what is needed for current commands

**Minimum config surface**

- `CLAW_PLAID_LEDGER_DB_PATH`
- `CLAW_PLAID_LEDGER_WORKSPACE_PATH`
- `PLAID_CLIENT_ID`
- `PLAID_SECRET`
- `PLAID_ENV`

**Done when**

- Config can be loaded from environment and validated
- Missing required values produce a clear error message
- Database path is configurable
- Tests cover successful load and validation failures

**Notes**

- Start with plain environment-variable loading
- Do not add Pydantic unless there is a compelling reason
- Keep config logic deterministic and easy to test

---

### Task 4: Add SQLite bootstrap and schema initialization

**Why**

SQLite is the source of truth. Before Plaid sync exists, the repo needs
a real database file and schema.

**Scope**

- Add database initialization code using standard-library `sqlite3`
- Create the SQLite file on first run
- Create the initial schema
- Make initialization safe to run multiple times

**Done when**

- `ledger init-db` creates the SQLite file and schema
- Re-running `ledger init-db` is safe and idempotent
- Schema creation uses explicit SQL checked into source control
- Tests verify first-run and rerun behavior

**Notes**

- No ORM
- Keep migrations primitive for now; an initial schema bootstrap is enough
- If a migration mechanism is introduced, keep it minimal

---

### Task 5: Add schema tests

**Why**

Schema bugs are boring, dangerous little gremlins. Catch them early.

**Scope**

- Add tests that initialize a temporary database
- Verify required tables exist
- Verify rerunning initialization does not fail
- Verify key uniqueness and required constraints where practical

**Done when**

- Schema tests pass
- Tests cover idempotent initialization
- Tests verify the intended unique identifiers exist

---

## Proposed initial schema

The schema should stay minimal and support:
- stable transaction identity
- account storage
- persisted sync cursor/state
- future reconciliation of pending vs posted transactions

### Table: `accounts`

Purpose: store account metadata associated with imported transactions.

Suggested columns:

- `id` INTEGER PRIMARY KEY
- `plaid_account_id` TEXT NOT NULL UNIQUE
- `name` TEXT NOT NULL
- `mask` TEXT
- `type` TEXT
- `subtype` TEXT
- `institution_name` TEXT
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

### Table: `transactions`

Purpose: store imported Plaid transactions using stable upstream IDs.

Suggested columns:

- `id` INTEGER PRIMARY KEY
- `plaid_transaction_id` TEXT NOT NULL UNIQUE
- `plaid_account_id` TEXT NOT NULL
- `amount` NUMERIC NOT NULL
- `iso_currency_code` TEXT
- `name` TEXT NOT NULL
- `merchant_name` TEXT
- `pending` INTEGER NOT NULL
- `authorized_date` TEXT
- `posted_date` TEXT
- `raw_json` TEXT
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Suggested near-term follow-up:
- add a foreign key to `accounts(plaid_account_id)` if that does not
  complicate early imports unnecessarily

### Table: `sync_state`

Purpose: persist cursor-based sync state for Plaid transaction sync.

Suggested columns:

- `id` INTEGER PRIMARY KEY
- `item_id` TEXT NOT NULL UNIQUE
- `cursor` TEXT
- `last_synced_at` TEXT

## Acceptance criteria for the sprint

- Config can be loaded from environment and validated
- DB path is configurable
- `ledger init-db` creates the SQLite file and schema
- Re-running init is safe and idempotent
- Schema tests pass
- Docs consistently describe the chosen CLI framework

## Explicitly deferred

- Real Plaid API calls
- First transaction sync implementation
- Markdown exports for OpenClaw
- OpenClaw wake-up / notification
- Merchant rules
- Reconciliation logic beyond schema support
- Webhook support
