# Sprint 7 — M6: Multi-institution management

## Sprint goal

Support the full household account structure without requiring manual `.env` switching per
sync run. Introduce an `items.toml` config file that lists every Plaid item in the
household. `ledger sync --all` iterates through all items sequentially, each with its own
cursor and access token. `ledger sync --item <id>` syncs a single named item. The existing
no-flag `ledger sync` path — one item from env vars — continues to work unchanged.

An `owner` tag (e.g. `alice`, `bob`, `shared`) is stored on `sync_state` and `accounts`
rows so Hestia can answer household-scoped vs. individual-scoped queries by filtering on
`account_id` without any change to the transactions table or the Agent API.

## Working agreements

- Keep changes small and independently reviewable.
- Prefer one standalone task per PR unless a dependency forces a pair.
- Preserve strict quality gates on every PR:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest`
- Use standard-library `sqlite3` for the database layer (no change from M5).
- Use standard-library `tomllib` for TOML parsing — it ships with Python 3.12+
  (already the project's minimum version) and requires no new runtime dependency.
- Add appropriate unit and integration tests for each task.

## Conventions and implementation notes

**Owner tag:** A free-form string stored in `sync_state.owner` and `accounts.owner`.
Defaults to `None` for the legacy single-item env-var path. Hestia infers per-account
ownership by querying accounts for a given item, not by filtering transactions directly.
This is a naming convention, not a schema constraint. No validation of the owner string is
performed.

**Schema migration:** `initialize_database` uses `CREATE TABLE IF NOT EXISTS`, which does
not add columns to existing tables. Task 1 extends `initialize_database` with an explicit
migration step that runs `ALTER TABLE ... ADD COLUMN` inside a `try/except
sqlite3.OperationalError` to handle both fresh and existing databases idempotently.

**`items.toml` format:**

```toml
[[items]]
id = "bank-alice"
access_token_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
owner = "alice"

[[items]]
id = "bank-bob"
access_token_env = "PLAID_ACCESS_TOKEN_BANK_BOB"
owner = "bob"

[[items]]
id = "bank-shared"
access_token_env = "PLAID_ACCESS_TOKEN_BANK_SHARED"
owner = "shared"

[[items]]
id = "card-alice"
access_token_env = "PLAID_ACCESS_TOKEN_CARD_ALICE"
owner = "alice"
```

- `id` (str, required): operator-assigned identifier; used as the `item_id` key in
  `sync_state`
- `access_token_env` (str, required): name of the environment variable that holds the
  Plaid access token for this item (the token value is never written to the file)
- `owner` (str, optional): free-form tag, e.g. `"alice"`, `"bob"`, `"shared"`

**Plaid client adapter construction for multi-item:** `PlaidClientAdapter.from_config`
already accepts a `Config` object and reads only `plaid_client_id`, `plaid_secret`, and
`plaid_env` — it does not store `plaid_access_token`. The adapter is constructed once and
reused across all items in `--all`. Per-item access tokens are resolved from the named env
vars listed in `items.toml` and passed to `run_sync` (and on to `adapter.sync_transactions`)
at call time.

**`require_plaid_client` flag on `load_config`:** The existing `require_plaid=True` flag
validates all four Plaid vars including `PLAID_ACCESS_TOKEN`. Multi-item commands need
only the three shared credentials. A new `require_plaid_client: bool = False` flag checks
`PLAID_CLIENT_ID`, `PLAID_SECRET`, and `PLAID_ENV` without requiring `PLAID_ACCESS_TOKEN`.

**`sync --all` error handling:** if a single item fails (network error, invalid token,
missing env var), log the error at `ERROR`, increment a failure counter, and continue to
the next item. Do not abort the whole run. On completion, print a summary line:
`sync --all: N items synced, M failed` — and exit with code 1 if `M > 0`, code 0 otherwise.

**`sync --item` and `sync --all` are mutually exclusive:** validate this at the start of
the command and exit with code 2 if both are passed.

**Per-item sync output format:**

- `sync[bank-alice]: accounts=3 added=5 modified=1 removed=0`
- `sync[card-bob]: ERROR <message>`

---

## Task breakdown

### Task 1: Schema migration and DB helpers for `owner` ✅ DONE

**Scope**

Extend the database layer to store and retrieve the `owner` tag on `sync_state` and
`accounts`. No CLI or sync-engine changes in this task.

**`schema.sql` additions**

Add `owner TEXT` to both tables:

```sql
CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY,
    item_id TEXT NOT NULL UNIQUE,
    cursor TEXT,
    owner TEXT,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    plaid_account_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    mask TEXT,
    type TEXT,
    subtype TEXT,
    institution_name TEXT,
    owner TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**`db.py` changes**

1. `initialize_database` — add a migration step after the `executescript` call. Run
   both `ALTER TABLE` statements inside `try/except sqlite3.OperationalError` so the
   function is idempotent on existing databases:

   ```python
   for stmt in (
       "ALTER TABLE accounts ADD COLUMN owner TEXT",
       "ALTER TABLE sync_state ADD COLUMN owner TEXT",
   ):
       try:
           connection.execute(stmt)
       except sqlite3.OperationalError:
           pass  # column already exists
   ```

2. `NormalizedAccountRow` — add `owner: str | None` field.

3. `normalize_account_for_db` — add `owner: str | None = None` keyword parameter; set
   it on the returned `NormalizedAccountRow`.

4. `upsert_account` — add `owner: str | None = None` keyword parameter; pass it through
   to `normalize_account_for_db` and include it in the `INSERT` column list and the
   `ON CONFLICT DO UPDATE SET` clause.

5. `upsert_sync_state` — add `owner: str | None = None` keyword parameter; include it
   in the `INSERT` column list and the `ON CONFLICT DO UPDATE SET` clause.

6. New dataclass `SyncStateRow` (frozen):

   ```python
   @dataclass(frozen=True)
   class SyncStateRow:
       """One row from sync_state with owner and last-synced timestamp."""
       item_id: str
       owner: str | None
       last_synced_at: str | None
   ```

7. New function `get_all_sync_state`:

   ```python
   def get_all_sync_state(
       connection: sqlite3.Connection,
   ) -> list[SyncStateRow]:
       """Return all sync_state rows ordered by item_id."""
   ```

   Query: `SELECT item_id, owner, last_synced_at FROM sync_state ORDER BY item_id`.
   Returns an empty list if the table has no rows.

**Done when**

- `initialize_database` is idempotent: calling it on a fresh DB and on a DB that already
  has the old schema both result in a valid DB with `owner` columns on both tables
- `upsert_account(..., owner="alice")` stores `owner="alice"` and subsequent reads confirm
  the value
- `upsert_sync_state(..., owner="bob")` stores `owner="bob"` and subsequent reads
  confirm the value
- `get_all_sync_state` returns all rows, ordered by `item_id`, with correct `owner` and
  `last_synced_at` values
- `mypy --strict` passes; `ruff` passes

**Testing expectations**

All new tests in `test_db.py`.

- Test: `initialize_database` on a fresh DB → `owner` column present on both tables
- Test: `initialize_database` on an existing DB without `owner` column → column added,
  existing data preserved
- Test: `initialize_database` called twice on the same DB → no error (idempotent)
- Test: `upsert_account` with `owner="alice"` → row readable with `owner="alice"`; a
  second `upsert_account` with `owner="bob"` for the same `plaid_account_id` → row
  updated to `owner="bob"`
- Test: `upsert_account` with no `owner` argument → `owner` stored as `None`
- Test: `upsert_sync_state` with `owner="shared"` → row readable with `owner="shared"`
- Test: `get_all_sync_state` with multiple rows → returned list is ordered by `item_id`

---

### Task 2: `ItemConfig` and `items.toml` loader ✅ DONE

**Scope**

Create `src/claw_plaid_ledger/items_config.py` with a typed dataclass and loader
function for the multi-item TOML config. This module is the only place that reads
`items.toml`.

**`items_config.py` contents**

```python
"""Multi-item Plaid configuration loader (items.toml)."""

DEFAULT_ITEMS_TOML: Path  # = Path("~/.config/claw-plaid-ledger/items.toml").expanduser()


class ItemsConfigError(ValueError):
    """Raised when items.toml is malformed or missing a required field."""


@dataclass(frozen=True)
class ItemConfig:
    """Configuration for one Plaid item (institution + access token)."""
    id: str
    access_token_env: str
    owner: str | None = None


def load_items_config(path: Path | None = None) -> list[ItemConfig]:
    """Load and parse items.toml; return an empty list if the file is absent."""
```

**Implementation requirements**

1. **File not found:** if the resolved path does not exist, return `[]`. Do not raise.

2. **TOML parsing:** use `tomllib.load()` (stdlib, Python 3.12+). Open the file in
   binary mode: `with path.open("rb") as fh: data = tomllib.load(fh)`.

3. **Structure validation:**
   - The top-level key is `"items"` containing a list of tables.
   - Each table must have a string `"id"` and a string `"access_token_env"`.
   - `"owner"` is optional; absent or `null` → `owner=None`.
   - If any entry is missing `"id"` or `"access_token_env"`, raise `ItemsConfigError`
     with a message that identifies the index: e.g.
     `"items[1] missing required field 'access_token_env'"`.
   - If the top-level `"items"` key is absent, return `[]`.

4. **Return type:** `list[ItemConfig]` in the same order as the file.

5. **Imports:** `tomllib` (stdlib), `dataclasses`, `pathlib`, no new runtime deps.

**Done when**

- `load_items_config()` with no file at the default path returns `[]`
- `load_items_config(path)` with a valid file returns correctly typed `ItemConfig` list
- A file with a missing required field raises `ItemsConfigError`
- `mypy --strict` passes on the module

**Testing expectations**

All new tests in `test_items_config.py` using `tmp_path` for file creation.

- Test: file not found → returns `[]`
- Test: empty file (or file with no `[[items]]` entries) → returns `[]`
- Test: one item, all fields present → single `ItemConfig` with correct values
- Test: multiple items → list in file order; each `ItemConfig` has correct values
- Test: item with `owner` absent → `owner` is `None`
- Test: item missing `"id"` → `ItemsConfigError` raised with index in message
- Test: item missing `"access_token_env"` → `ItemsConfigError` raised
- Test: `id` is not a string (e.g. integer) → `ItemsConfigError` raised

---

### Task 3: Propagate `owner` through `run_sync()` ✅ DONE

**Scope**

Thread the `owner` parameter from the call site down through `run_sync` to the database
writes. This task depends on Task 1 (db layer owner support). One file changes:
`sync_engine.py`.

**`sync_engine.py` change**

Add `owner: str | None = None` to the `run_sync` signature:

```python
def run_sync(
    *,
    db_path: Path,
    adapter: SyncAdapter,
    access_token: str,
    item_id: str = DEFAULT_ITEM_ID,
    owner: str | None = None,
) -> SyncSummary:
```

Inside the function body:

1. Pass `owner=owner` to `upsert_account`:

   ```python
   for account in result.accounts:
       upsert_account(connection, account, owner=owner)
   ```

2. Pass `owner=owner` to `upsert_sync_state`:

   ```python
   upsert_sync_state(
       connection,
       item_id=item_id,
       cursor=cursor,
       owner=owner,
   )
   ```

No other changes. The return type, error handling, cursor invariant, and all existing
behaviour are unchanged.

**Done when**

- `run_sync(..., owner="alice")` causes the `sync_state` row for that `item_id` to have
  `owner="alice"` and all accounts seen in that sync to have `owner="alice"`
- `run_sync(...)` called without `owner` (legacy path) stores `owner=None`
- All existing `test_sync_engine.py` tests continue to pass without modification
- `mypy --strict` passes

**Testing expectations**

New tests in `test_sync_engine.py`.

- Test: `run_sync` with `owner="bob"` → `sync_state` row has `owner="bob"`;
  accounts returned from the adapter have `owner="bob"` in the DB
- Test: `run_sync` without `owner` (default) → `sync_state` row has `owner=None`;
  accounts have `owner=None`

---

### Task 4: `load_config` client flag and `ledger sync --item` / `--all`

**Scope**

Two changes that must ship together: (1) a new `require_plaid_client` flag on
`load_config` so multi-item commands can validate shared Plaid credentials without
requiring `PLAID_ACCESS_TOKEN`; (2) `--item` and `--all` options on the `sync` CLI
command. This task depends on Tasks 2 and 3.

**`config.py` change**

Add `require_plaid_client: bool = False` keyword parameter to `load_config`. When
`require_plaid_client=True`, check `PLAID_CLIENT_ID`, `PLAID_SECRET`, and `PLAID_ENV`
but not `PLAID_ACCESS_TOKEN`. The existing `require_plaid=True` path is unchanged:

```python
def load_config(
    environ: dict[str, str] | None = None,
    *,
    require_plaid: bool = False,
    require_plaid_client: bool = False,
    env_file: Path | None = None,
) -> Config:
```

Inside the validation block:

```python
if require_plaid or require_plaid_client:
    if not plaid_client_id:
        missing.append("PLAID_CLIENT_ID")
    if not plaid_secret:
        missing.append("PLAID_SECRET")
    if not plaid_env:
        missing.append("PLAID_ENV")
if require_plaid:
    if not plaid_access_token:
        missing.append("PLAID_ACCESS_TOKEN")
```

**`cli.py` changes**

Add two optional parameters to the `sync` command:

```python
@app.command()
def sync(
    item: Annotated[str | None, typer.Option("--item", help="Sync a single item from items.toml by ID.")] = None,
    all_items: Annotated[bool, typer.Option("--all", help="Sync all items listed in items.toml.")] = False,
) -> None:
```

Three execution paths:

**Path A — no flags (existing behaviour, unchanged):**

```
ledger sync
```

- `load_config(require_plaid=True)` as before; error if `PLAID_ACCESS_TOKEN` not set
- `PlaidClientAdapter.from_config(config)`
- `run_sync(db_path=..., adapter=..., access_token=config.plaid_access_token, item_id=config.item_id)`
- Print: `sync: accounts=X added=Y modified=Z removed=W`

**Path B — single named item:**

```
ledger sync --item bank-alice
```

- Validate `--item` and `--all` are mutually exclusive; exit 2 if both passed
- `load_items_config()` → find entry with matching `id`; if not found:
  `typer.echo(f"sync: item '{item}' not found in items.toml")` and exit 2
- Resolve the access token: `token = os.environ.get(item_cfg.access_token_env)`;
  if absent: `typer.echo(f"sync: {item_cfg.access_token_env} is not set")` and exit 2
- `load_config(require_plaid_client=True)` to validate shared Plaid credentials; on
  `ConfigError`, echo and exit 2
- `adapter = PlaidClientAdapter.from_config(config)`
- `run_sync(db_path=config.db_path, adapter=adapter, access_token=token, item_id=item_cfg.id, owner=item_cfg.owner)`
- Print: `sync[{item_cfg.id}]: accounts=X added=Y modified=Z removed=W`

**Path C — all items:**

```
ledger sync --all
```

- Validate mutual exclusivity with `--item`; exit 2 if both passed
- `load_items_config()` → if empty list:
  `typer.echo("sync --all: no items found in items.toml")` and exit 2
- `load_config(require_plaid_client=True)`; on `ConfigError`, echo and exit 2
- `adapter = PlaidClientAdapter.from_config(config)` — constructed once, reused
- For each `item_cfg` in the list (sequential, no concurrency):
  - Resolve `token = os.environ.get(item_cfg.access_token_env)`; if absent:
    `typer.echo(f"sync[{item_cfg.id}]: ERROR {item_cfg.access_token_env} is not set")`
    and increment failure counter; `continue`
  - Call `run_sync(...)` inside `try/except Exception`; on error:
    `typer.echo(f"sync[{item_cfg.id}]: ERROR {exc}")` and increment failure counter;
    `continue`
  - On success:
    `typer.echo(f"sync[{item_cfg.id}]: accounts={s.accounts} added={s.added} modified={s.modified} removed={s.removed}")`
    and increment success counter
- Print final summary: `sync --all: {success_count} items synced, {failure_count} failed`
- Exit 1 if `failure_count > 0`, else exit 0

**`.env.example` update**

Update the `CLAW_PLAID_LEDGER_ITEM_ID` comment block to reference `items.toml`:

```
# Plaid item identifier for single-item mode. Used by `ledger sync` (no flags).
# Defaults to "default-item". For multi-institution households, create
# ~/.config/claw-plaid-ledger/items.toml and use `ledger sync --all`.
CLAW_PLAID_LEDGER_ITEM_ID=
```

**Done when**

- `ledger sync` (no flags) behaves identically to Sprint 6 behaviour
- `ledger sync --item bank-alice` syncs only that item using the token from the named
  env var and stores `owner` from items.toml
- `ledger sync --all` syncs all items sequentially; a single-item failure does not abort
  other items; exits 1 if any item failed
- `ledger sync --item foo --all` prints an error and exits 2
- `ledger sync --item nonexistent` prints an error and exits 2
- `load_config(require_plaid_client=True)` validates the three shared credentials but
  not `PLAID_ACCESS_TOKEN`
- All quality gates pass

**Testing expectations**

New tests in `test_cli.py` (or a dedicated `test_sync_cli.py`). Use
`unittest.mock.patch` to stub `run_sync`, `load_items_config`, and
`PlaidClientAdapter.from_config`.

- Test: `load_config(require_plaid_client=True)` with `PLAID_CLIENT_ID` missing →
  `ConfigError` naming the missing variable
- Test: `load_config(require_plaid_client=True)` with all three client vars set but
  `PLAID_ACCESS_TOKEN` absent → no error
- Test: `ledger sync --item bank-alice` (item in items.toml, token env set) →
  `run_sync` called with `item_id="bank-alice"`, `owner="alice"`, correct `access_token`
- Test: `ledger sync --item missing-id` → exits 2, error message includes `"missing-id"`
- Test: `ledger sync --item bank-alice` (token env not set) → exits 2, error references
  the env var name
- Test: `ledger sync --all` (two items, both succeed) → `run_sync` called twice; output
  includes both item IDs; exits 0
- Test: `ledger sync --all` (two items, one raises) → `run_sync` called twice; output
  contains `ERROR` for the failing item; exits 1; success item is still printed
- Test: `ledger sync --all` (items.toml empty) → exits 2
- Test: `ledger sync --item foo --all` → exits 2

---

### Task 5: `doctor` extension for per-item sync state

**Scope**

Extend the `doctor` command in `cli.py` to report per-item sync state. This task
depends on Task 1 (`get_all_sync_state`) and Task 2 (`load_items_config`).

**New output block**

After the existing `sync_state rows=` / `accounts rows=` / `transactions rows=` lines,
add a per-item section.

If `items.toml` is **absent or empty** (i.e. `load_items_config()` returns `[]`):

```
doctor: items.toml not found — single-item mode
```

If `items.toml` has items, print one line per item, cross-referenced with `sync_state`.
For an item that has been synced:

```
doctor: item bank-alice owner=alice last_synced_at=2024-01-15T08:30:00+00:00
```

For an item that appears in `items.toml` but has no row in `sync_state` yet:

```
doctor: item card-bob owner=bob last_synced_at=never
```

If `sync_state` has rows for item IDs that are **not** in `items.toml` (orphans from
single-item or legacy runs), print them with a note:

```
doctor: item default-item owner=None last_synced_at=2024-01-10T06:00:00+00:00 [not in items.toml]
```

**Implementation notes**

- Call `get_all_sync_state(conn)` to retrieve all `SyncStateRow` objects; build a dict
  keyed by `item_id`.
- Call `load_items_config()` outside the `sqlite3.connect` block.
- For each `ItemConfig` in the loaded list: look up the `item_id` in the dict; if found,
  print with `last_synced_at` from the row; if not found, print `last_synced_at=never`.
- After printing items from items.toml, iterate over any `SyncStateRow` whose `item_id`
  is not in the items.toml list and print with the `[not in items.toml]` suffix.
- Do not call `sys.exit(1)` for any of these states — per-item sync status is informational
  only.
- Catch `ItemsConfigError` from `load_items_config()` and print:
  `doctor: items.toml [WARN] parse error: {e}` — then skip the per-item block entirely.
  Do not exit non-zero.

**Done when**

- `doctor` with no `items.toml` prints the single-item-mode notice and exits 0
- `doctor` with a valid `items.toml` prints one line per configured item with correct
  `owner` and `last_synced_at`
- An item in `items.toml` not yet synced shows `last_synced_at=never`
- Orphaned `sync_state` rows (not in items.toml) are printed with the `[not in items.toml]`
  suffix
- A malformed `items.toml` prints a `[WARN]` notice without crashing `doctor`
- All existing doctor checks remain unaffected

**Testing expectations**

New tests in `test_cli.py`. Use `tmp_path` for DB and items.toml files, and patch
`load_items_config` where needed.

- Test: `items.toml` absent → output contains `"single-item mode"`; exit code 0
- Test: `items.toml` with two items, both synced → output contains both item IDs with
  correct `last_synced_at`
- Test: item in `items.toml` not yet in `sync_state` → output shows
  `"last_synced_at=never"` for that item
- Test: orphan row in `sync_state` (not in items.toml) → output contains
  `"[not in items.toml]"` for that item
- Test: malformed `items.toml` (raises `ItemsConfigError`) → output contains
  `"items.toml [WARN]"`; exit code 0

---

### Task 6: `ARCHITECTURE.md` update

**Scope**

Update `ARCHITECTURE.md` to document the M6 design. No code changes; documentation only.

**Sections to add or update**

1. **Current milestone focus** — update from M5 to M6; note Sprint 7 added multi-item
   support via `items.toml`, `ledger sync --all`, and per-account `owner` tagging.

2. **Repository layout** — add `items_config.py` to the `src/claw_plaid_ledger/`
   listing with a one-line description.

3. **Components** — add `items_config.py` to the component list:
   `Multi-item config loader (items_config.py)` — parses `items.toml` and returns a
   typed list of `ItemConfig` objects.

4. **New section: Multi-institution management** — add after the OpenClaw notification
   section. Cover:
   - Purpose: sync multiple Plaid items (one per institution/owner) from one command
   - `items.toml` location, format, and required/optional fields (with example)
   - How `ledger sync --all` iterates items, constructs one shared adapter, and resolves
     per-item access tokens from named env vars
   - Per-item error handling: single-item failure does not abort the run; exit code 1 if
     any item failed
   - The `owner` tag: what it is, where it is stored (`sync_state.owner`,
     `accounts.owner`), and how Hestia uses it (filter on `account_id` after learning
     which accounts belong to which item — no change to `transactions` or the Agent API)
   - Legacy single-item path (`CLAW_PLAID_LEDGER_ITEM_ID` + `PLAID_ACCESS_TOKEN`) remains
     valid and writes `owner=None`
   - Design decision recorded: owner scoping is a naming convention on `item_id`, not a
     `transactions` column

5. **Configuration table** — add:

   | Variable | Required | Default | Description |
   |---|---|---|---|
   | `CLAW_PLAID_LEDGER_ITEM_ID` | no | `default-item` | Item ID for single-item mode (legacy path) |

   Document `items.toml` as a separate config file (not an env var), with its default
   path `~/.config/claw-plaid-ledger/items.toml` and the fields table from the sprint
   conventions above.

6. **Data flow** — extend to show the multi-item path:

   ```
   items.toml ─┐
               ├─ ledger sync --all ─> [bank-alice] run_sync -> SQLite
               │                    -> [bank-bob]  run_sync -> SQLite
               │                    -> [card-alice] run_sync -> SQLite
   PLAID_ENV   ─┘
   ```

**Done when**

- A developer unfamiliar with the codebase can understand the full multi-item sync flow
  from `ARCHITECTURE.md` alone
- The `items.toml` format, the `owner` field semantics, and the per-item error handling
  are all documented
- The design decision (owner as naming convention, no transactions schema change) is
  captured with rationale

---

## Acceptance criteria for the sprint

- `ledger sync` (no flags) with `PLAID_ACCESS_TOKEN` in env behaves identically to
  Sprint 6; no regression
- `ledger sync --item <id>` syncs a single named item from `items.toml`, using the
  access token from the named env var, and stores the owner tag on `sync_state` and
  `accounts`
- `ledger sync --all` syncs every item in `items.toml` sequentially; exits 0 if all
  succeed, 1 if any fail; failing items do not abort the run
- `ledger sync --item x --all` exits 2 with a clear error message
- `owner` is stored on `sync_state` and `accounts` rows for every item synced via
  `--item` or `--all`; the legacy no-flag path stores `owner=None`
- `ledger doctor` lists per-item sync state when `items.toml` is present, including
  `owner` and `last_synced_at` per item; falls back gracefully when the file is absent
- `initialize_database` is idempotent on existing databases that lack the `owner` column
- All four quality gates pass on every PR:
  `ruff format`, `ruff check`, `mypy --strict`, `pytest`
- `ARCHITECTURE.md` documents the multi-item flow, `items.toml` format, owner semantics,
  and legacy path

## Explicitly deferred

- Parallel/concurrent sync across items (explicitly out of scope in the roadmap;
  tracked as a future milestone)
- Per-item notification routing (no change to the OpenClaw notification path from M5;
  all items share the same `OPENCLAW_HOOKS_*` config)
- Validation or enumeration of allowed `owner` string values (free-form string by design)
- Webhook-triggered `sync --all` (the webhook background sync still uses the single-item
  env-var path; multi-item webhook routing is future work)
- `GET /accounts` endpoint surfacing the `owner` field (Agent API is unchanged; Hestia
  queries accounts via `account_id` and infers owner from item context)
