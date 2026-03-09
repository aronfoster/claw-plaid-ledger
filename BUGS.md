# Known Bugs

Tracked here instead of GitHub Issues to keep context accessible to LLM
coding agents. Each entry includes location, impact, and a suggested fix so
an agent can act on it without needing to reconstruct the diagnosis.

---

## BUG-001 — `account_count` inflated on multi-page syncs

**Status:** Open
**Severity:** Low (operator output only; no data integrity impact)
**File:** `src/claw_plaid_ledger/sync_engine.py`
**Introduced:** Sprint 2

### Description

Plaid returns the full account list on every page of a `transactions/sync`
response. The sync loop accumulates `account_count` on each iteration, so
the final `SyncSummary.accounts` value is multiplied by the number of pages
fetched rather than reflecting the actual number of distinct accounts.

The upsert logic is correct and idempotent — no duplicate rows are written.
Only the operator-facing summary number is wrong.

### Reproduction

Run `ledger sync` against an Item that requires more than one page
(`has_more=True` on the first response). The reported `accounts=N` will be
a multiple of the true account count.

### Suggested fix

Option A — deduplicate by account ID before counting:

```python
seen_account_ids: set[str] = set()

# inside the loop, replace:
for account in result.accounts:
    upsert_account(connection, account)
account_count += len(result.accounts)

# with:
for account in result.accounts:
    upsert_account(connection, account)
    seen_account_ids.add(account.plaid_account_id)

# after the loop:
account_count = len(seen_account_ids)
```

Option B — query the DB for `COUNT(DISTINCT plaid_account_id)` after the
loop completes. Slightly more accurate if deletions are ever added, but adds
a round-trip.

Option A is preferred for Sprint 3.

---

## BUG-002 — `src/typer.py` is a custom shim, not the real Typer library

**Status:** Open
**Severity:** Medium (will cause friction when adding new CLI options)
**File:** `src/typer.py`, `pyproject.toml`, `ARCHITECTURE.md`
**Introduced:** Sprint 1

### Description

`ARCHITECTURE.md` and `SPRINT.md` both describe Typer as the CLI framework.
`pyproject.toml` lists no `typer` dependency. The file `src/typer.py` is a
hand-rolled shim that shadows the real `typer` package and only implements
`count`-style options via `argparse` internally.

This works for the current command surface, but the shim will need to be
extended every time a new option type is needed (flags, string args, dates,
etc.). The real Typer library handles all of this and is the stated intent.

### Impact

Any LLM agent reading `ARCHITECTURE.md` will assume real Typer semantics
and may write code that imports Typer features (e.g. `typer.Option`,
`typer.Argument`) that the shim does not implement, causing silent failures
or import errors.

### Suggested fix

Make a deliberate decision and document it clearly:

**Option A — adopt real Typer:**
1. Add `typer` to `pyproject.toml` dependencies
2. Delete `src/typer.py`
3. Update `cli.py` imports (`from typer import ...` instead of local shim)
4. Update `ARCHITECTURE.md` to confirm real Typer is now in use

**Option B — keep the shim intentionally:**
1. Rename `src/typer.py` to something unambiguous (e.g. `src/cli_framework.py`)
2. Update imports in `cli.py`
3. Update `ARCHITECTURE.md` to describe this as a minimal internal CLI
   framework, not Typer, and explain why

Option A is recommended. The shim offers no advantage over the real library
and creates a maintenance burden as the CLI grows.
