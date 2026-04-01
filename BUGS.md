# Known Bugs

Tracked here instead of GitHub Issues to keep context accessible to LLM
coding agents. Each entry includes location, impact, and a suggested fix so
an agent can act on it without needing to reconstruct the diagnosis.

---

## Active bugs

### BUG-016 — Skill doctor command pipes curl through `python3 -c`, blocking allowlist approval and causing agent context loss

**Status:** Active
**Severity:** High (Hestia and Athena cannot use the ledger skill without manual per-run Discord/TUI approval; Hestia loses session context on every approved run)
**Area:** Skill definitions (`hestia-ledger/SKILL.md`, `athena-ledger/SKILL.md`) / OpenClaw exec approval compatibility
**Reported by:** Operator (diagnosed during OpenClaw 2026.3.31 upgrade, which introduced exec approvals)

#### What is happening

The OpenClaw doctor health check for both ledger skills generates a command of the form:

```bash
source ~/.openclaw/.env && curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "$CLAW_LEDGER_URL/transactions?..." | python3 -c "import json, sys; ..."
```

This command uses a pipe (`|`) to pass curl output through a `python3 -c` inline eval for display formatting. The same pattern is used at runtime when agents (particularly Hestia) call the ledger API.

Two properties of this command make it permanently incompatible with OpenClaw's exec allowlist system:

1. **Pipe operator blocked in allowlist mode.** OpenClaw's `security=allowlist` exec policy supports `&&`/`||`/`;` chaining but explicitly does not support redirections, and `|` (pipe) falls into that category. A command containing a pipe can never satisfy the allowlist and always requires a prompt.

2. **`allow-always` cannot persist this command.** Because the pipe makes the command an allowlist miss on every run, `allow-always` approvals are not persisted. Each new invocation (with different date parameters or query strings) triggers a fresh approval prompt.

The consequence: every time Hestia or Athena calls the ledger API, OpenClaw emits an approval request and the exec tool returns immediately with `status: approval-pending`. The agent's turn ends without a result. When the operator approves via Discord and the command completes, OpenClaw delivers the output as a new disconnected agent turn. Hestia receives the raw formatted output with no memory of the original question and dumps it verbatim into the chat.

#### Fix

Remove the `| python3 -c "..."` pipe from the doctor health check command and from agent runtime usage. Both skill SKILL.md files should instruct agents to call `curl` and receive raw JSON — LLM agents have no difficulty reading and acting on raw JSON responses, and the python3 formatting step provides no value to the model.

With the pipe removed, the command becomes:

```bash
source ~/.openclaw/.env && curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "$CLAW_LEDGER_URL/transactions?..."
```

This form uses only `&&` (supported chaining) and no inline eval. `allow-always` can persist it, subsequent runs auto-approve, and exec stays synchronous so agents receive results inline within the same turn.

---

## Resolved bugs (recent)

---

### BUG-017 — `_patch_today` in spend-trends tests targeted the wrong module namespace, causing date-dependent CI failures

**Status:** Resolved (branch `claude/add-bug-016-exec-pipe-allowlist`)
**Severity:** Medium (tests passed locally but failed on CI whenever the calendar month rolled over)
**Area:** `tests/test_server_spend_trends.py`
**Reported by:** Operator (CI failure observed in April 2026 after tests were written in March 2026)

#### What was happening

`_patch_today` in `test_server_spend_trends.py` called:

```python
monkeypatch.setattr("claw_plaid_ledger.routers.utils._today", lambda: fixed)
```

`spend.py` imports `_today` via `from claw_plaid_ledger.routers.utils import _today`, which binds the name in `spend.py`'s own namespace. Patching `utils._today` replaced the attribute on the `utils` module but left the already-bound reference in `spend.py` untouched, so the trends endpoint continued calling the real `date.today()`.

The bug was invisible locally: the tests were written in March 2026, the fixed patch date was `2026-03-15`, and the real date was also in March 2026 — so the month-level assertions matched by coincidence. When CI ran in April 2026, the real date shifted the 6-month window by one month and four tests failed.

#### Fix

Changed the patch target to `claw_plaid_ledger.routers.spend._today`, which is where the name is actually resolved at call time.

---

### BUG-015 — M21 allocation backfill silently dropped all annotation data

**Status:** Resolved (branch `claude/restore-transaction-categories-bbO0l`)
**Severity:** High (all pre-M21 category, note, and tag data invisible to the allocation model)
**Area:** Database (`db.py` — `initialize_database`)
**Reported by:** Hestia

#### What happened

The M21 (Sprint 23) startup backfill inserted allocation stubs for every
transaction that had no allocation row — but the INSERT selected only from
`transactions`, with no JOIN to `annotations`. Every transaction annotated
before M21 received a stub with `category = NULL`, `note = NULL`,
`tags = NULL`, even though the data was sitting intact in `annotations`.

Because the `NOT EXISTS` guard made the backfill idempotent, subsequent
startups could not repair the damage: the allocation row already existed, so
the INSERT never fired again.

The same null-stub pattern existed in `upsert_transaction` (called during
sync), but that path is not affected by this bug because `PUT /annotations`
always calls `upsert_single_allocation` immediately after, keeping both
tables in sync for post-M21 writes.

#### Affected code

- `src/claw_plaid_ledger/db.py` — `initialize_database`, backfill block
  (lines ~48–63 before fix)

#### Fix

Two changes to `initialize_database`:

1. **INSERT backfill** now `LEFT JOIN`s `annotations` and projects
   `an.category`, `an.note`, `an.tags` into new allocation rows. Stubs
   created from this point forward carry annotation data.

2. **UPDATE migration** (new) runs on every startup and repairs stubs that
   were already created with all-null annotatable fields where annotation
   data exists. Guards prevent it from touching allocations that already
   have data, split transactions (allocation count > 1), or rows with no
   matching annotation.

Nine tests added to `TestStartupBackfill` in `tests/test_db.py`, covering
both paths (with and without tags), the no-annotation case, the
no-overwrite guard, the split-transaction guard, and idempotency.

---

### BUG-014 — Unknown query parameters are silently ignored

**Status:** Resolved (Sprint 21, M19)
**Severity:** High (callers receive unexpected results with no indication that their parameters were dropped)
**Area:** API (all GET endpoints that accept query parameters)
**Reported by:** Operator (agents using wrong pagination parameter names received full unfiltered datasets)

#### What is happening

FastAPI silently drops any query parameter that does not match a declared
field on the endpoint's Pydantic model or function signature. A caller passing
`?page=2&page_size=50` to `GET /transactions` gets the same response as
`GET /transactions` with no parameters at all — the full dataset at the default
limit — with HTTP 200 and no indication that `page` and `page_size` were
ignored.

BUG-012 (`range` silently dropped on `GET /transactions`) was a specific
instance of this class of problem, resolved by a code-ordering fix. This bug
covers the general case: any misspelled or misremembered parameter name on any
endpoint silently degrades into a no-op filter.

#### Required behavior

When a request contains one or more query parameter names that the endpoint
does not recognise, the server must return HTTP 422 with a response body that
lists:

1. The unrecognised parameter names.
2. The full set of valid parameter names for that endpoint.

This gives agents and callers an actionable error message rather than
misleading data.

#### Required work

**Add a reusable strict-params dependency to `server.py`:**

```python
from collections.abc import Callable

def _strict_params(allowed: frozenset[str]) -> Callable[[Request], None]:
    """Raise 422 if the request contains any query parameter not in allowed."""
    def _check(request: Request) -> None:
        unknown = sorted(set(request.query_params.keys()) - allowed)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unrecognized query parameters",
                    "unrecognized": unknown,
                    "valid_parameters": sorted(allowed),
                },
            )
    return _check
```

**Wire it into each parameterised GET endpoint** as an additional entry in the
`dependencies=[...]` list. Valid parameter sets per endpoint:

| Endpoint | Valid parameters |
|---|---|
| `GET /transactions` | `start_date`, `end_date`, `account_id`, `pending`, `min_amount`, `max_amount`, `keyword`, `view`, `limit`, `offset`, `search_notes`, `tags`, `range` |
| `GET /spend` | `start_date`, `end_date`, `owner`, `tags`, `include_pending`, `view`, `account_id`, `category`, `tag`, `range` |
| `GET /spend/trends` | `months`, `owner`, `tags`, `include_pending`, `view`, `account_id`, `category`, `tag` |
| `GET /errors` | `hours`, `min_severity`, `limit`, `offset` |

`GET /health`, `GET /accounts`, `GET /categories`, `GET /tags` accept no
query parameters and do not need the dependency (FastAPI already ignores extras
on parameterless endpoints, and adding an empty allowlist would break health
checks from monitoring tools that append cache-buster params).

**Add tests** to the appropriate `test_server_*.py` file for each affected
endpoint confirming:

- A request with a valid-but-misspelled parameter (e.g. `?offest=10`) returns
  HTTP 422.
- The 422 body contains `"unrecognized"` and `"valid_parameters"` keys.
- A request with all-valid parameters is unaffected (no regression).

#### Notes

- `PUT /annotations/{transaction_id}` and `PUT /accounts/{account_id}` accept
  no query parameters; Pydantic already rejects unknown fields in request
  bodies via `model_config = ConfigDict(extra="forbid")` if that is not already
  set — check and add if missing.
- `POST /webhooks/plaid` accepts no query parameters and is called by Plaid
  infrastructure; do not add strict param checking there.

---

### BUG-013 — Annotations absent from `GET /transactions` list results

**Status:** Resolved (M16a, out-of-sprint patch)
**Severity:** Medium (callers cannot see annotation data without one extra request per transaction)
**Area:** API (`GET /transactions`)
**Reported by:** Athena

`GET /transactions/{id}` returns a nested `annotation` field (`category`,
`note`, `tags`, `updated_at`) via `_fetch_transaction_with_annotation()`.
`GET /transactions` (the list endpoint) calls `query_transactions()` from
`db.py`, which performs no JOIN to the `annotations` table. Every transaction
in list results is returned without annotation data, even when annotations
exist.

**Affected code:**
- `src/claw_plaid_ledger/server.py` — `list_transactions()` (lines ~536–571)
  and `_fetch_transaction_with_annotation()` helper (lines ~754–779)
- `src/claw_plaid_ledger/db.py` — `query_transactions()` (lines ~365–463)

**Fix:** Extracted `_annotation_from_joined_row()` helper to keep
`query_transactions()` complexity within linter thresholds. Extended the SELECT
to project `ann.category`, `ann.note`, `ann.tags`, `ann.updated_at`, and
`ann.plaid_transaction_id` (presence sentinel) from the LEFT JOIN that was
already in place. Each row now carries `"annotation": {...}` when an annotation
exists, or `"annotation": null` otherwise — field-for-field identical to the
shape returned by `GET /transactions/{id}`.

---

### BUG-012 — `range` parameter ignored on `GET /transactions`

**Status:** Resolved (M16a, out-of-sprint patch)
**Severity:** High (date-scoped transaction queries silently return the full dataset)
**Area:** API (`GET /transactions`)
**Reported by:** Athena

`GET /spend` accepts a `range` query parameter (`last_month`, `this_month`,
`last_30_days`, `last_7_days`) and resolves it to a bounded date window via
`_resolve_spend_dates()` before querying the database. `GET /transactions` has
no equivalent — `TransactionListQuery` only exposes explicit `start_date` and
`end_date` strings. Passing `range=last_month` (or any range value) to
`/transactions` is silently discarded by FastAPI. The endpoint then runs with
no date filter and returns the full transaction dataset, paginated at the
default limit of 100 (max 500).

**Observed behaviour:**
- `/spend?range=last_month` → correctly returns spend aggregated over Feb 1–28.
- `/transactions?range=last_month` → ignores the parameter, returns 671
  transactions spanning the full history.

**Root cause:** `_SpendRange` was defined *after* `list_transactions()` in
`server.py`. With `from __future__ import annotations`, FastAPI resolves type
hints via `get_type_hints()` at decoration time — when `_SpendRange` was not
yet in the module namespace. The annotation was stored as an unresolved
`ForwardRef`, causing FastAPI to silently skip validation and parameter binding.

**Fix:** Moved `_SpendRange = Literal[...]` to before `TransactionListQuery`
and `list_transactions()` so FastAPI can resolve the annotation at decoration
time. Added `date_range: Annotated[_SpendRange | None, Query(alias="range")]`
as an explicit parameter on `list_transactions()`. When supplied, it is
resolved to `start_date`/`end_date` via the existing `_resolve_spend_dates()`
helper. Explicit dates still take precedence (same convention as `GET /spend`).
Invalid values are rejected with HTTP 422.

---

### BUG-004 — Agents do not auto-discover skills in their personal skills folder

**Status:** Resolved (Sprint 16, M14)
**Severity:** Medium (skill is deployed but invisible to the agent until manually registered)
**Area:** OpenClaw agent setup / RUNBOOK

After copying a skill bundle into an agent's skills directory (e.g.
`~/.openclaw/workspace/agents/athena/skills/athena-ledger/`), the agent has
no awareness of the skill at session start. It must be explicitly pointed to
the skill via `TOOLS.md`. Athena worked around this herself by adding a
`## Skills (Private / Athena-Specific)` section to her `TOOLS.md`, which
documents the skill path, purpose, auth, and server requirements. Hestia's
`TOOLS.md` has not been updated and likely has the same gap.

**Options for resolution:**

**Option A — RUNBOOK instruction (manual, low-effort):**
Add a step to the skill registration section of the RUNBOOK instructing
operators to append a skills block to each agent's `TOOLS.md` after running
`sync-skills.sh push`. Provide a copy-paste template modelled on Athena's
existing entry.

**Option B — Script-generated TOOLS.md injection (automated):**
Extend `scripts/sync-skills.sh` (or add a separate script) to read each
skill's frontmatter (`name`, `description`, `primaryEnv`) and upsert a
matching entry into the target agent's `TOOLS.md` under a `## Skills`
section. On `pull`, the injected block could be left in place or stripped.
This keeps TOOLS.md in sync automatically but requires parsing SKILL.md
frontmatter and careful idempotent upsert logic.

**Reference:** Athena's `~/.openclaw/workspace/agents/athena/TOOLS.md`
(Skills section) is the current working example of what a correct manual
entry looks like.

**Fix:** `scripts/sync-skills.sh push` now idempotently injects a `## Skills`
block (sourced from each SKILL.md frontmatter) into the target agent's
`TOOLS.md` after copying skill files. RUNBOOK.md Section 16 documents the
workflow and manual fallback.

---

### BUG-005 — Account IDs are opaque numbers; no human-readable names or descriptions

**Status:** Resolved (Sprint 17, M15)
**Severity:** Medium (agents and operators cannot identify accounts without manual ID mapping)
**Area:** Database schema, API (`/accounts`)

Plaid account IDs are numeric and carry no human context. There is no
`accounts` table, no `GET /accounts` endpoint, and no way to associate a
name (e.g. "Chase Checking") or description (e.g. "joint household account")
with an account ID. Agents working with transaction data must maintain manual
ID-to-name mappings out of band (Athena's `TOOLS.md` notes this gap
explicitly).

**Required work:**

1. **`account_labels` table** — operator/agent-editable store keyed on Plaid
   account ID with `name` (short label) and `description` (free-text) columns.
   Should be additive: rows can be absent for unlabelled accounts without
   breaking anything.

2. **`GET /accounts`** — returns all known Plaid account IDs seen in
   transactions, joined with any matching `account_labels` rows. Missing label
   rows should surface as `null`/empty fields, not errors.

3. **`PUT /accounts/{account_id}`** — upserts `name` and `description` for a
   given account ID. Primary write surface for agents and operators.

**Fix:** Added `account_labels` table (idempotent `CREATE TABLE IF NOT EXISTS`)
with `label` and `description` columns keyed on Plaid account ID. `GET /accounts`
returns all known accounts LEFT JOINed with label data. `PUT /accounts/{account_id}`
upserts label data and returns the full account record; returns 404 for unknown IDs.

---

### BUG-006 — `PUT /annotations/{transaction_id}` returns `{"status": "ok"}` instead of the updated record

**Status:** Resolved (Sprint 16, M14)
**Severity:** Low (UX friction; callers must issue a follow-up GET to confirm the write)
**Area:** API (`PUT /annotations/{transaction_id}`)

After a successful annotation write the endpoint returns a minimal
`{"status": "ok"}` body. Callers (agents and scripts) have no way to confirm
the final merged state of the record without issuing a separate
`GET /transactions/{id}`.

**Fix:** After applying the annotation update, the endpoint now fetches and
returns the full transaction record (same shape as `GET /transactions/{id}`).
No schema change required.

---

### BUG-007 — No endpoints to enumerate existing category or tag values

**Status:** Resolved (Sprint 16, M14)
**Severity:** Medium (agents must guess or infer valid values from transaction samples, risking inconsistent tagging)
**Area:** API

There is no way to query what category or tag values are already in use across
the ledger. Agents annotating transactions have to infer the vocabulary from
sampled transaction data, which leads to duplicate or near-duplicate values
(e.g. `groceries` vs `grocery`).

**Required work:**

1. **`GET /categories`** — returns the distinct set of category values
   present across all annotations, sorted alphabetically.

2. **`GET /tags`** — returns the distinct set of tag values present across
   all annotations, sorted alphabetically.

**Fix:** `GET /categories` returns distinct non-null category values sorted
alphabetically. `GET /tags` returns distinct tag values unnested from all
annotation rows, sorted alphabetically. Both are simple `SELECT DISTINCT`
queries with no schema changes.

---

### BUG-008 — `GET /spend` has no `account_id` filter

**Status:** Resolved (Sprint 17, M15)
**Severity:** Medium (per-card breakdowns require manual ID tracking out of band)
**Area:** API (`GET /spend`)

`GET /spend` aggregates across all accounts with no way to scope to a single
account. Producing a per-card breakdown requires either multiple filtered
`GET /transactions` calls and manual summation, or maintaining an account ID
mapping outside the ledger.

**Fix:** Added `account_id` query parameter to `GET /spend`. When present,
restricts the aggregation to transactions belonging to the specified Plaid
account ID via a direct `plaid_account_id` match (no JOIN required).

---

### BUG-009 — `GET /spend` has no `category` or `tag` filter

**Status:** Resolved (Sprint 17, M15)
**Severity:** Medium (category/tag rollups require paginating all transactions and summing manually)
**Area:** API (`GET /spend`)

There is no way to request spend totals scoped to a category or tag (e.g.
`GET /spend?category=Software&tag=recurring`). Agents must paginate
`GET /transactions`, filter client-side, and sum amounts manually — which is
slow and error-prone for large datasets.

**Fix:** Added `category` (case-insensitive match via `LOWER(ann.category) = LOWER(?)`)
and `tag` (case-insensitive match via `json_each`) query parameters to `GET /spend`.
All three new filters (`account_id`, `category`, `tag`) are AND-combined with each
other and with the existing `owner` and `tags` parameters.

---

### BUG-010 — `GET /spend` requires explicit dates; no relative range shorthand

**Status:** Resolved (Sprint 16, M14)
**Severity:** Low (minor ergonomic friction for common queries)
**Area:** API (`GET /spend`)

`GET /spend` mandates `start_date` and `end_date` in every request. Common
queries like "last month" or "last 30 days" require the caller to compute and
format both dates explicitly, which is inconvenient interactively and verbose
in agent prompts.

**Fix:** Added optional `range` parameter to `GET /spend` accepting
`last_month`, `this_month`, `last_30_days`, and `last_7_days`. Server derives
`start_date`/`end_date` from the shorthand using server local time and echoes
the resolved dates in the response. Explicit dates continue to work unchanged
and take precedence when provided alongside `range`.

---

### BUG-011 — No spend trends endpoint for month-over-month analysis

**Status:** Resolved (Sprint 18, M16)
**Severity:** Medium (trend analysis requires multiple `/spend` calls and manual stitching)
**Area:** API (`GET /spend/trends`)

There is no endpoint that returns spend aggregated by calendar month.
Producing a month-over-month view currently requires one `GET /spend` call
per month, then manually stitching results — tedious for agents and operators
alike.

**Required work:**

Add `GET /spend/trends` (preferred, keeps spend endpoints grouped) with the
following behaviour:

- **Response shape:** array of objects ordered oldest → newest:
  ```json
  [
    {"month": "2026-01", "total_spend": 3241.50, "transaction_count": 47, "partial": false},
    {"month": "2026-02", "total_spend": 1876.00, "transaction_count": 31, "partial": true}
  ]
  ```
- **`partial: true`** on the current calendar month so callers know not to
  compare it directly against complete months.
- **`months` param** — integer lookback window (default `6`, e.g. `?months=12`
  or `?months=3`). Counts back from the current month inclusive.
- **Filter parity with `GET /spend`:** `owner`, `tags`, `category`,
  `account_id` (once BUG-008 is resolved), `view`, `include_pending` — all
  applied consistently so a trend query and a point-in-time spend query over
  the same filters are directly comparable.

**Fix:** Implemented `GET /spend/trends` as a GROUP BY month query over the
same filtered transaction set used by `GET /spend`. Returns a plain JSON
array of `{month, total_spend, allocation_count, partial}` objects ordered
oldest → newest, zero-filled for months with no matching transactions. The
`months` parameter (default 6, minimum 1) controls the lookback window.
Supports all seven filters from `GET /spend`. No schema changes required.
(Note: the count field was originally named `transaction_count`; renamed to
`allocation_count` in M21/Sprint 23 to reflect allocation-row semantics.)

---

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
