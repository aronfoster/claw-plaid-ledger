# Sprint 10 — M9: Canonical household views (source precedence)

## Sprint goal

Solve joint-account overlap deterministically. By the end of this sprint the
operator can declare, in configuration, exactly which item is the canonical
source for any shared account; `ledger apply-precedence` writes that
decision to the DB; and `GET /transactions` defaults to a clean
canonical household view while keeping raw records fully accessible.
Sprint 10 is complete when suppression is config-driven, auditable, and the
agent API exposes canonical transactions by default.

## Working agreements

- Keep each task reviewable in one PR where possible.
- Preserve backward compatibility for all existing sync, doctor, serve, and
  items workflows.
- Raw ingestion must remain complete; suppression happens only in canonical
  query/view layers — the sync engine never deletes records.
- Run the quality gate before every commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Add or update tests for every behavior change.
- No new runtime dependencies without explicit justification.

## Task breakdown

---

### Task 1: Config extension — `suppressed_accounts` in `items.toml`

**Scope**

Extend `items.toml` and `ItemConfig` so that each item can declare which of
its own Plaid accounts are superseded by the canonical view from another item.
This is the configuration foundation that all subsequent tasks depend on.

**Config shape**

Add an optional `[[items.suppressed_accounts]]` sub-table to each `[[items]]`
block:

```toml
[[items]]
id                = "bank-alice"
access_token_env  = "PLAID_ACCESS_TOKEN_BANK_ALICE"
owner             = "alice"

  [[items.suppressed_accounts]]
  plaid_account_id     = "plaid_acct_YYYY"   # account in THIS item to suppress
  canonical_account_id = "plaid_acct_XXXX"   # canonical replacement (from card-bob)
  canonical_from_item  = "card-bob"          # optional — for human auditability
  note = "AmEx shared card — alice is authorized user; bob's view is canonical"
```

**Implementation notes**

1. **New dataclass** — add `SuppressedAccountConfig` to `items_config.py`:

   ```python
   @dataclass(frozen=True)
   class SuppressedAccountConfig:
       plaid_account_id: str        # account in this item being suppressed
       canonical_account_id: str    # the winning canonical account ID
       canonical_from_item: str | None = None  # documentation only
       note: str | None = None                 # documentation only
   ```

2. **Extend `ItemConfig`** — add field:

   ```python
   suppressed_accounts: tuple[SuppressedAccountConfig, ...] = ()
   ```

   Use a tuple (not list) to keep the frozen dataclass immutable.

3. **Update `_parse_item`** — parse the optional `suppressed_accounts` list of
   sub-tables. Both `plaid_account_id` and `canonical_account_id` are required
   strings; `canonical_from_item` and `note` are optional strings. Raise
   `ItemsConfigError` on type violations; silently skip if the key is absent
   (backward compat).

4. **Update `items.toml.example`** — add one example `suppressed_accounts`
   block under the `bank-alice` item to show the alice/bob household
   configuration. Keep the existing three-item structure intact.

**Done when**

- `load_items_config` parses items that have no `suppressed_accounts` without
  change (full backward compat).
- Items with one or more `suppressed_accounts` entries return correctly typed
  `SuppressedAccountConfig` tuples on `ItemConfig.suppressed_accounts`.
- `ItemsConfigError` is raised for missing required fields
  (`plaid_account_id`, `canonical_account_id`) and for wrong types on any
  field.
- `items.toml.example` has a concrete suppressed-accounts example.
- Tests cover: no suppressed_accounts (backward compat), single entry, multiple
  entries, missing required field error, wrong-type error.
- All quality gates pass.

---

### Task 2: DB schema and `ledger apply-precedence` command

**Scope**

Add a `canonical_account_id` column to the `accounts` table to persist
suppression provenance, and deliver a `ledger apply-precedence` CLI command
that reads the config aliases and writes that decision to the DB. This is the
layer that makes suppression durable and auditable.

**Implementation notes**

1. **Schema change** — add `canonical_account_id TEXT` to `accounts` in
   `schema.sql`:

   ```sql
   CREATE TABLE IF NOT EXISTS accounts (
       ...
       canonical_account_id TEXT,   -- non-null = suppressed; points to canonical account
       ...
   );
   ```

   Also add the column via `ALTER TABLE` migration in `initialize_database`
   (same suppression-safe pattern as the existing `owner` / `item_id`
   migrations):

   ```python
   "ALTER TABLE accounts ADD COLUMN canonical_account_id TEXT",
   ```

2. **Update `NormalizedAccountRow`** — add `canonical_account_id: str | None = None`
   field to the dataclass.

3. **Update `upsert_account`** — include `canonical_account_id` in the INSERT
   and the `ON CONFLICT DO UPDATE SET` clause.

4. **New `db.py` helper** — add `apply_account_precedence`:

   ```python
   def apply_account_precedence(
       connection: sqlite3.Connection,
       items: list[ItemConfig],
   ) -> int:
       """
       Write canonical_account_id to suppressed accounts.

       For each SuppressedAccountConfig across all items, sets
       accounts.canonical_account_id = canonical_account_id WHERE
       plaid_account_id = suppressed plaid_account_id.

       Returns the count of rows updated.
       """
   ```

   Updates only rows that exist in the DB; silently skips config entries whose
   account has not yet been synced (the operator can re-run after syncing).
   Also clears `canonical_account_id` to NULL for any account that is no
   longer mentioned as suppressed in the current config (config is the single
   source of truth).

5. **New CLI command** — `ledger apply-precedence`:

   ```
   $ ledger apply-precedence
   apply-precedence: loaded 2 alias(es) from items.toml
   apply-precedence: updated 1 account(s)
   apply-precedence: 1 alias(es) skipped — account not yet in DB (sync first)
   apply-precedence: done
   ```

   - Loads `items.toml` (exits 0 with a message if absent or empty).
   - Opens the DB connection.
   - Calls `apply_account_precedence` and prints the summary.
   - Also clears `canonical_account_id` on all accounts NOT listed as
     suppressed in the current config (handles alias removal).
   - Exits 0 on success, 1 on DB or config error.

**Done when**

- `initialize_database` adds `canonical_account_id` to `accounts` without
  breaking an existing database (idempotent migration).
- `upsert_account` persists `canonical_account_id` correctly.
- `apply_account_precedence` sets the column for known accounts, skips unknown
  ones, clears stale suppressions.
- `ledger apply-precedence` runs end-to-end and prints accurate counts.
- Tests cover: no aliases in config (no-op), alias for account in DB, alias
  for account not yet in DB (skip + count), stale alias clearing (alias
  removed from config clears DB column), idempotent re-run.
- All quality gates pass.

---

### Task 3: Canonical query layer and API defaults

**Scope**

Update `query_transactions` to support a canonical-only view that excludes
transactions from suppressed accounts, make `GET /transactions` default to this
canonical view (with a `?view=raw` opt-out), and expose suppression provenance
on `GET /transactions/{id}`.

**Implementation notes**

1. **Update `TransactionQuery`** — add `canonical_only: bool = True`:

   ```python
   @dataclass(frozen=True)
   class TransactionQuery:
       ...
       canonical_only: bool = True
   ```

2. **Update `query_transactions`** — when `canonical_only=True`, add a JOIN
   and filter:

   ```sql
   JOIN accounts a ON a.plaid_account_id = t.plaid_account_id
   WHERE a.canonical_account_id IS NULL
   ...
   ```

   When `canonical_only=False`, omit the JOIN and filter (returns all
   transactions regardless of suppression status). The total count must
   reflect the same filter as the rows query.

3. **Update `GET /transactions` in `server.py`** — add `view` query parameter:

   | Parameter | Type | Default | Description |
   |---|---|---|---|
   | `view` | `"canonical"` \| `"raw"` | `"canonical"` | `canonical` hides suppressed-account transactions; `raw` returns all records |

   Map `view="raw"` to `canonical_only=False` in the `TransactionQuery`.
   Reject invalid values with HTTP 422.

4. **Update `GET /transactions/{id}` response** — when the requested
   transaction belongs to an account with `canonical_account_id` set, include
   a `suppressed_by` field in the response:

   ```json
   {
     "id": "...",
     ...
     "suppressed_by": "plaid_acct_XXXX"
   }
   ```

   `suppressed_by` is `null` (not present in the canonical view but
   accessible via raw queries) when the account is not suppressed. This gives
   agents auditable provenance when they fetch a raw transaction.

   Implementation: `get_transaction` in `db.py` should JOIN `accounts` and
   return `canonical_account_id` alongside the existing fields.

**Done when**

- `query_transactions` with default `canonical_only=True` excludes
  transactions whose account has a non-null `canonical_account_id`.
- `query_transactions` with `canonical_only=False` returns all transactions.
- `GET /transactions` defaults to `?view=canonical` and correctly excludes
  suppressed-account transactions.
- `GET /transactions?view=raw` returns the full unfiltered set.
- `GET /transactions?view=invalid` returns HTTP 422.
- `GET /transactions/{id}` for a suppressed-account transaction includes
  `"suppressed_by": "<canonical_account_id>"`.
- `GET /transactions/{id}` for a canonical-account transaction returns
  `"suppressed_by": null`.
- All existing `GET /transactions` tests pass without modification (default
  canonical view is backward compatible because no accounts are suppressed in
  existing test fixtures unless explicitly set).
- New tests cover: canonical filter in query layer, `?view=raw` opt-out,
  `?view=invalid` 422, `suppressed_by` in detail response.
- All quality gates pass.

---

### Task 4: `ledger overlaps` command

**Scope**

Deliver a `ledger overlaps` CLI command that gives the operator a clear view of
configured suppression rules, their current DB state, and any accounts that
*might* be unconfigured overlaps — so the operator can decide whether to add
config entries for them.

**User-facing output**

```
$ ledger overlaps

Configured suppressions (from items.toml):
  bank-alice / plaid_acct_YYYY  →  suppressed by plaid_acct_XXXX (card-bob)  [IN DB]
  bank-alice / plaid_acct_ZZZZ  →  suppressed by plaid_acct_WWWW (card-bob)  [NOT YET SYNCED — run sync first]

Potential unconfirmed overlaps (same name + mask from different items):
  "Premium Rewards"  mask=4321  type=credit  items: bank-alice, card-bob  — consider adding suppressed_accounts config

overlaps: 1 configured suppression active, 1 pending sync, 1 potential overlap flagged.
```

**Implementation notes**

1. **Configured suppressions section** — for every `SuppressedAccountConfig`
   across all items:
   - Look up the suppressed `plaid_account_id` in the `accounts` table.
   - Show `IN DB` if found (and `canonical_account_id` is correctly set),
     `MISMATCH` if found but `canonical_account_id` differs from config
     (stale — operator should re-run `apply-precedence`), or
     `NOT YET SYNCED` if the account is not in the DB at all.

2. **Potential unconfirmed overlaps section** — query the DB for groups of
   accounts with identical `name`, `mask`, and `type` but different `item_id`
   values. These are candidate shared accounts that the operator might not
   have configured yet. Show them as informational suggestions only — no
   automated action.

3. **Behavior**:
   - If `items.toml` is absent or has no `suppressed_accounts` entries,
     print `overlaps: no suppressions configured` and exit 0.
   - If the DB is not reachable, print the error and exit 1.
   - Always exits 0 if the command ran (regardless of MISMATCH/NOT_YET_SYNCED
     status — this is a display command).

**Done when**

- `ledger overlaps` renders configured suppression entries with correct status
  (`IN DB`, `MISMATCH`, `NOT YET SYNCED`).
- Potential unconfirmed overlaps are detected and displayed when accounts with
  the same name/mask/type exist across multiple items.
- No-config path exits cleanly with a message.
- Tests cover: no items.toml, configured suppression IN DB, configured
  suppression NOT YET SYNCED, configured suppression MISMATCH (stale),
  potential unconfirmed overlaps detected, no unconfirmed overlaps (clean
  household).
- All quality gates pass.

---

### Task 5: Sprint closeout, docs, and acceptance validation

**Scope**

Update all project documentation to reflect the M9 implementation, rename any
stale "deduplication" terminology to "source precedence", and validate
acceptance criteria.

**Checklist**

- `ARCHITECTURE.md`:
  - Add `canonical_account_id` to the `accounts` schema table.
  - Add `apply-precedence` and `overlaps` to the CLI interfaces table.
  - Update the data-flow diagram to show the canonical view layer between
    raw DB records and the Agent API.
  - Rename any mention of "deduplication" to "source precedence /
    household identity".
  - Update the repository layout section with any new modules.
- `RUNBOOK.md`:
  - Add a "Household source precedence setup" section that walks the operator
    through: sync all items → configure `suppressed_accounts` in
    `items.toml` → run `ledger apply-precedence` → verify with
    `ledger overlaps`.
  - Add `ledger apply-precedence` and `ledger overlaps` to the command
    reference.
- `ROADMAP.md`:
  - Move M9 from "Upcoming Milestones" to "Completed Milestones".
- `SPRINT.md`:
  - Append `✅ DONE` to each completed task heading.
  - Add "Sprint 10 closeout ✅ DONE" section summarising what shipped and
    any explicitly deferred follow-ups.
- Quality gate must pass at closeout:
  - `uv run --locked ruff format . --check` ✅
  - `uv run --locked ruff check .` ✅
  - `uv run --locked mypy .` ✅
  - `uv run --locked pytest -v` ✅

---

## Acceptance criteria for Sprint 10

- `items.toml` supports `[[items.suppressed_accounts]]` sub-tables with
  `plaid_account_id`, `canonical_account_id`, and optional
  `canonical_from_item` / `note` fields.
- `ledger apply-precedence` reads the config and writes `canonical_account_id`
  to suppressed account rows, clears stale suppressions, and reports counts.
- `GET /transactions` defaults to the canonical view (suppressed-account
  transactions hidden); `?view=raw` restores the unfiltered set.
- `GET /transactions/{id}` for a suppressed-account transaction includes
  `suppressed_by` provenance.
- `ledger overlaps` shows configured suppression status and surfaces potential
  unconfirmed overlaps.
- Raw ingestion is untouched: the sync engine never deletes or alters
  transactions based on suppression config.
- All existing workflows (`doctor`, `sync`, `serve`, `items`, `link`,
  `preflight`) are unbroken.
- Quality gate passes.

## Explicitly deferred (remain out of scope in Sprint 10)

- Multi-item webhook routing (M10).
- Automated `apply-precedence` on every sync (can be added in M10 or later).
- Transfer detection and internal movement suppression (post-M9 backlog).
- Parallel multi-institution sync.
- Operator review queue UI beyond the `ledger overlaps` display command.
