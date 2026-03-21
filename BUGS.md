# Known Bugs

Tracked here instead of GitHub Issues to keep context accessible to LLM
coding agents. Each entry includes location, impact, and a suggested fix so
an agent can act on it without needing to reconstruct the diagnosis.

---

## Active bugs

---

### BUG-004 — Agents do not auto-discover skills in their personal skills folder

**Status:** Active
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

---

### BUG-005 — Account IDs are opaque numbers; no human-readable names or descriptions

**Status:** Active
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

**Suggested fix:** Add an Alembic migration for `account_labels`, implement
the two endpoints, and decorate `GET /transactions` and `GET /spend` responses
to inline `account_name` where available (or keep it join-only on
`GET /accounts` to avoid response bloat — TBD).

---

### BUG-006 — `PUT /annotations/{transaction_id}` returns `{"status": "ok"}` instead of the updated record

**Status:** Active
**Severity:** Low (UX friction; callers must issue a follow-up GET to confirm the write)
**Area:** API (`PUT /annotations/{transaction_id}`)

After a successful annotation write the endpoint returns a minimal
`{"status": "ok"}` body. Callers (agents and scripts) have no way to confirm
the final merged state of the record without issuing a separate
`GET /transactions/{id}`.

**Suggested fix:** After applying the annotation update, fetch the full
transaction record and return it as the 200 response body, matching the shape
of `GET /transactions/{id}`. No schema change required.

---

### BUG-007 — No endpoints to enumerate existing category or tag values

**Status:** Active
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

**Suggested fix:** Both endpoints are simple `SELECT DISTINCT` queries over
the annotations store with no new schema changes. Response shape can be a
plain sorted array of strings.

---

### BUG-008 — `GET /spend` has no `account_id` filter

**Status:** Active
**Severity:** Medium (per-card breakdowns require manual ID tracking out of band)
**Area:** API (`GET /spend`)

`GET /spend` aggregates across all accounts with no way to scope to a single
account. Producing a per-card breakdown requires either multiple filtered
`GET /transactions` calls and manual summation, or maintaining an account ID
mapping outside the ledger.

**Suggested fix:** Add an optional `account_id` query parameter that, when
present, restricts the spend aggregation to transactions belonging to that
account. Pairs naturally with BUG-005 (`GET /accounts`) once account labels
are available.

---

### BUG-009 — `GET /spend` has no `category` or `tag` filter

**Status:** Active
**Severity:** Medium (category/tag rollups require paginating all transactions and summing manually)
**Area:** API (`GET /spend`)

There is no way to request spend totals scoped to a category or tag (e.g.
`GET /spend?category=Software&tag=recurring`). Agents must paginate
`GET /transactions`, filter client-side, and sum amounts manually — which is
slow and error-prone for large datasets.

**Suggested fix:** Add optional `category` and `tag` query parameters to
`GET /spend`. When both are supplied they are applied as AND filters, further
narrowing the aggregation. Each should be case-insensitive and consistent with
the vocabulary surfaced by `GET /categories` and `GET /tags` (BUG-007).

---

### BUG-010 — `GET /spend` requires explicit dates; no relative range shorthand

**Status:** Active
**Severity:** Low (minor ergonomic friction for common queries)
**Area:** API (`GET /spend`)

`GET /spend` mandates `start_date` and `end_date` in every request. Common
queries like "last month" or "last 30 days" require the caller to compute and
format both dates explicitly, which is inconvenient interactively and verbose
in agent prompts.

**Suggested fix:** Add an optional `range` parameter accepting shorthands such
as `last_month`, `this_month`, `last_30_days`, and `last_7_days`. When
`range` is supplied, `start_date` / `end_date` should be optional and derived
server-side. Explicit dates should continue to work unchanged and take
precedence if provided alongside `range`.

---

### BUG-011 — No spend trends endpoint for month-over-month analysis

**Status:** Active
**Severity:** Medium (trend analysis requires multiple `/spend` calls and manual stitching)
**Area:** API (`GET /spend/trends` or `GET /trends`)

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

**Suggested fix:** Implement as a GROUP BY month query over the same filtered
transaction set used by `GET /spend`, using the same canonical/raw view logic.
No new schema changes required.

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
