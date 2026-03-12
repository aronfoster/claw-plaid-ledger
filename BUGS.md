# Known Bugs

Tracked here instead of GitHub Issues to keep context accessible to LLM
coding agents. Each entry includes location, impact, and a suggested fix so
an agent can act on it without needing to reconstruct the diagnosis.

---

## Active bugs

None.

---

## Resolved bugs

---

### BUG-003 — `ledger serve` auth reads `CLAW_API_SECRET` from `os.environ` instead of `load_config()`

**Status:** Resolved (fix-bug-003)
**Severity:** High (auth failure when secret is in .env but not shell env)
**File:** `src/claw_plaid_ledger/server.py`

Bearer auth in `require_bearer_token()` read `CLAW_API_SECRET` directly from
`os.environ`, bypassing the config layer. Secrets set in
`~/.config/claw-plaid-ledger/.env` were silently ignored, causing 401s even
when the operator had correctly configured the file.

**Fix:** Added `load_api_secret()` to `config.py` (same `.env` + env merge
logic as `load_config()`, without requiring other vars). `require_bearer_token`
now calls `load_api_secret()` instead of `os.environ.get`.

---

### BUG-002 — `src/typer.py` is a custom shim, not the real Typer library

**Status:** Resolved (Sprint 3, Task 2)
**Severity:** Medium (will cause friction when adding new CLI options)
**File:** `src/typer.py`, `pyproject.toml`, `ARCHITECTURE.md`
**Introduced:** Sprint 1

`ARCHITECTURE.md` and `SPRINT.md` both describe Typer as the CLI framework.
`pyproject.toml` listed no `typer` dependency. The file `src/typer.py` was a
hand-rolled shim that shadowed the real `typer` package and only implemented
`count`-style options via `argparse` internally.

**Fix:** Adopted real Typer (Option A): added `typer` to `pyproject.toml`,
deleted `src/typer.py`, updated `cli.py` imports, updated `ARCHITECTURE.md`.

---

### BUG-001 — `account_count` inflated on multi-page syncs

**Status:** Resolved (Sprint 3, Task 1)
**Severity:** Low (operator output only; no data integrity impact)
**File:** `src/claw_plaid_ledger/sync_engine.py`
**Introduced:** Sprint 2

Plaid returns the full account list on every page of a `transactions/sync`
response. The sync loop accumulated `account_count` on each iteration, so
the final `SyncSummary.accounts` value was multiplied by the number of pages
fetched rather than reflecting the actual number of distinct accounts.

**Fix:** Deduplicated by account ID using a `seen_account_ids` set; set
`account_count = len(seen_account_ids)` after the loop (Option A).
