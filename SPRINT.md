# Sprint 16 — M14: API Quality-of-Life & Skill Discovery

## Sprint goal

Ship four focused improvements surfaced during the first production run of the
two-agent household. No schema changes. Every task is independently releasable.

## Why this sprint exists

M0–M13 delivered a full, production-deployed ledger. Usage revealed a set of
ergonomic gaps: annotation writes require a follow-up GET to confirm state,
agents must guess at valid category/tag vocabulary, every `/spend` call requires
manually computing dates, and newly pushed skills are invisible to agents until
TOOLS.md is updated by hand. M14 closes all four gaps with minimal surface area.

## Working agreements

- Each task ships as its own PR; no task blocks another.
- All Python changes must pass the quality gate before merge:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Script-only tasks (Task 4) still run the gate to confirm no regressions.
- Mark completed tasks `✅ DONE` in this file before committing.

---

## Task 1: BUG-006 — PUT /annotations returns the updated transaction record ✅ DONE

### Background

`PUT /annotations/{transaction_id}` currently returns `{"status": "ok"}`.
Callers must issue a follow-up `GET /transactions/{id}` to see the final merged
state. Returning the full record in the PUT response eliminates that round trip.

### Scope

**`server.py` — `PUT /annotations/{transaction_id}`**

After successfully upserting the annotation, fetch and return the full
transaction record using the same logic as `GET /transactions/{id}`. The
response body, status code (200), and error behaviour (404 when transaction
does not exist) are otherwise unchanged.

**Response shape** — identical to `GET /transactions/{id}`:

```json
{
  "id": "txn_abc123",
  "account_id": "acc_xyz",
  "amount": 42.50,
  "iso_currency_code": "USD",
  "name": "COFFEE SHOP",
  "merchant_name": "COFFEE SHOP",
  "pending": false,
  "date": "2026-03-01",
  "raw_json": null,
  "suppressed_by": null,
  "annotation": {
    "category": "food",
    "note": "morning coffee",
    "tags": ["discretionary"],
    "updated_at": "2026-03-21T09:00:00+00:00"
  }
}
```

The `annotation` block reflects the values just written — it will never be
`null` in a successful PUT response because the upsert just created it.

**Implementation note:** avoid duplicating the fetch logic. If `GET
/transactions/{id}` is implemented as a helper function or the fetch SQL is
already factored out in `db.py`, reuse it. If it is currently inlined in the
route handler, extract it into a shared helper as part of this task.

**`tests/test_server.py`**

Update or add tests covering:

- Successful PUT returns HTTP 200 with the full transaction shape (not
  `{"status": "ok"}`).
- The returned annotation block contains the values that were just written.
- PUT on a non-existent transaction still returns HTTP 404.
- A second PUT (update, not create) returns the newly updated annotation fields.

### Done when

- Quality gate passes.
- `PUT /annotations/{id}` response body matches `GET /transactions/{id}` shape.
- No follow-up GET is needed to confirm the write.

---

## Task 2: BUG-007 — GET /categories and GET /tags vocabulary endpoints ✅ DONE

### Background

Agents annotating transactions must infer valid category/tag values from
sampled transaction data. This leads to duplicates (`groceries` vs `grocery`).
Two read-only endpoints expose the vocabulary already present in annotations.

### Scope

**`server.py` — two new GET endpoints**

Both endpoints require the standard bearer-token auth (same as all other
endpoints). Both are read-only and require no request body or query parameters.

---

**`GET /categories`**

Returns the distinct set of non-null `category` values present across all rows
in the `annotations` table, sorted alphabetically (case-insensitive).

Response shape:

```json
{
  "categories": ["food", "software", "transport", "utilities"]
}
```

- Empty array if no annotations have a category set.
- SQL: `SELECT DISTINCT category FROM annotations WHERE category IS NOT NULL ORDER BY category COLLATE NOCASE`

---

**`GET /tags`**

Returns the distinct set of tag values across all annotations. Because `tags`
is stored as a JSON array string per row, the query must unnest using SQLite's
`json_each`:

```sql
SELECT DISTINCT j.value
FROM annotations a, json_each(a.tags) j
WHERE a.tags IS NOT NULL
ORDER BY j.value COLLATE NOCASE
```

Response shape:

```json
{
  "tags": ["discretionary", "needs-athena-review", "recurring", "subscription"]
}
```

- Empty array if no annotations have tags set.

---

**`db.py` — two new query functions**

Add `get_distinct_categories(conn) -> list[str]` and
`get_distinct_tags(conn) -> list[str]`. Keep the SQL in `db.py`; keep
the HTTP wiring in `server.py`.

**`tests/test_server.py`**

- `GET /categories` returns an alphabetically sorted list of distinct categories
  from existing annotations; excludes null categories.
- `GET /tags` returns an alphabetically sorted flat list of distinct tag values
  unnested from all annotation rows; excludes null/empty tags arrays.
- Both return `{"categories": []}` / `{"tags": []}` when the annotations
  table has no relevant data.
- Both require auth (unauthenticated request returns 401).

### Done when

- Quality gate passes.
- Both endpoints are listed in the auto-generated OpenAPI spec at `/openapi.json`.
- An agent can call `GET /categories` immediately after startup to retrieve
  the current vocabulary without sampling transactions.

---

## Task 3: BUG-010 — GET /spend relative date range shorthand

### Background

Every `/spend` call requires `start_date` and `end_date` in ISO format. Common
queries like "last month" force callers to compute and format both dates, which
is tedious interactively and verbose in agent prompts.

### Scope

**`server.py` — extend `GET /spend`**

Add an optional `range` query parameter:

```
range: Literal["last_month", "this_month", "last_30_days", "last_7_days"] | None = None
```

**Resolution rules (applied server-side at request time):**

1. If `range` is supplied, compute `start_date` and `end_date` from it using
   **server local time** (i.e. `datetime.now().date()`, not UTC).
2. If `start_date` is also supplied explicitly, it overrides the range-derived
   start date.
3. If `end_date` is also supplied explicitly, it overrides the range-derived
   end date.
4. If `range` is absent and either `start_date` or `end_date` is missing,
   return HTTP 422 with a clear message (existing behavior is unchanged).

**Date computation definitions (server local time):**

| `range` value  | `start_date`                          | `end_date`       |
|----------------|---------------------------------------|------------------|
| `this_month`   | First day of current month            | Today            |
| `last_month`   | First day of previous calendar month  | Last day of previous calendar month |
| `last_30_days` | Today − 30 days (inclusive)           | Today            |
| `last_7_days`  | Today − 7 days (inclusive)            | Today            |

"Previous calendar month" crosses year boundaries correctly (e.g. January →
December of the prior year).

**Response shape** — unchanged. Optionally surface the resolved dates in the
response body so callers can see what window was used:

```json
{
  "start_date": "2026-02-01",
  "end_date": "2026-02-28",
  "total_spend": 1842.00,
  "transaction_count": 34,
  "includes_pending": false,
  "filters": {
    "owner": null,
    "tags": []
  }
}
```

The `start_date` and `end_date` fields should always reflect the resolved
dates (whether derived from `range` or supplied explicitly) so callers can
confirm what window was actually used.

**Validation:**

- `range` with an unrecognised value → HTTP 422 (FastAPI handles this
  automatically via `Literal`).
- `range` absent, `start_date` present, `end_date` absent → HTTP 422.
- `range` absent, `end_date` present, `start_date` absent → HTTP 422.

**`tests/test_server.py`**

- `?range=this_month` returns a 200 with `start_date` set to the first of the
  current month and `end_date` set to today.
- `?range=last_month` returns the correct first/last day of the prior month,
  including the January→December year-boundary case.
- `?range=last_30_days` and `?range=last_7_days` return the correct computed
  window.
- Explicit `start_date` overrides the range-derived start; explicit `end_date`
  overrides the range-derived end.
- Omitting both `range` and `start_date`/`end_date` returns HTTP 422.
- An unrecognised `range` value returns HTTP 422.
- Existing calls with explicit `start_date` + `end_date` (no `range`) are
  unaffected.

**Tip:** freeze `datetime.now()` in tests (e.g. via `freezegun` or
`unittest.mock.patch`) so date-window assertions are deterministic.

### Done when

- Quality gate passes.
- `GET /spend?range=last_month` works end-to-end with no explicit dates.
- Explicit dates still work unchanged.
- Resolved `start_date` / `end_date` are visible in the response.

---

## Task 4: BUG-004 — Agent skill auto-discovery via sync-skills.sh

### Background

After `sync-skills.sh push` copies a skill bundle into an agent's skills
directory, the agent has no awareness of it. The agent's `TOOLS.md` must be
updated manually. Agents have no skills registered in their `TOOLS.md` today.

This task extends `sync-skills.sh push` to automatically inject (or update)
a `## Skills` section in each target agent's `TOOLS.md`, and adds a RUNBOOK
section documenting the workflow.

### Scope

**`scripts/sync-skills.sh` — extend `push`**

After the `rsync` copy step for each skill, upsert a skills entry in the target
agent's `TOOLS.md`.

The injected block must be:

- **Idempotent:** running `push` twice produces the same result. Use sentinel
  comments to identify the auto-generated block so it can be replaced, not
  appended. Recommended markers:
  ```
  <!-- sync-skills-start -->
  ...
  <!-- sync-skills-end -->
  ```
- **Derived from `SKILL.md` frontmatter only.** Read the following fields from
  the skill's `SKILL.md` YAML frontmatter block:
  - `name` — used as the section heading
  - `description` — one-line description
  - `metadata.openclaw.requires.env` — list of required environment variable
    names

The injected entry format (one entry per skill under the sentinel block):

```markdown
## Skills (managed by sync-skills.sh — do not edit between markers)

<!-- sync-skills-start -->
### hestia-ledger
- **Description:** Ingest and annotate claw-plaid-ledger transactions. Use after a Plaid sync to process new transactions, apply deterministic annotations, and escalate uncertain items to Athena via needs-athena-review tags. Reads and writes via the ledger HTTP API using bearer-token auth.
- **Skill path:** ~/.openclaw/workspace/agents/hestia/skills/hestia-ledger/SKILL.md
- **Required env:** `CLAW_API_SECRET`, `CLAW_LEDGER_URL`
<!-- sync-skills-end -->
```

If an agent has multiple skills, all entries appear between the same pair of
sentinels.

- **Non-destructive toward content outside the sentinel block.** The operator's
  hand-written TOOLS.md notes (cameras, SSH hosts, etc.) must be preserved.
- **Creates `TOOLS.md` if absent.** If the target agent directory exists but
  has no `TOOLS.md`, create one containing only the injected block (no default
  boilerplate).
- **`pull` behaviour:** leave the injected block in place on pull. The script
  does not strip or modify TOOLS.md on pull.

**Frontmatter parsing:** The SKILL.md files use YAML frontmatter delimited by
`---`. The script may use Python (`python3 -c "..."`) for parsing rather than
trying to wrangle YAML in pure bash — this is the recommended approach given
the nested structure of `metadata.openclaw.requires.env`.

**RUNBOOK.md — new section: Skill registration**

Add a section (placement: after the existing skills/sync-skills content, or
as a new top-level section if one does not exist) covering:

- What `sync-skills.sh push` now does end-to-end: copies skill files AND
  updates the agent's TOOLS.md.
- How to verify the injection worked: inspect
  `~/.openclaw/workspace/agents/<agent>/TOOLS.md` and confirm the `## Skills`
  section is present.
- How to re-run push after updating a skill definition (idempotent; safe to
  re-run at any time).
- Manual fallback: if `sync-skills.sh` is unavailable, the operator can
  copy the template below into the agent's TOOLS.md and fill in the fields
  from SKILL.md frontmatter.

  ```markdown
  ## Skills (managed by sync-skills.sh — do not edit between markers)

  <!-- sync-skills-start -->
  ### <name>
  - **Description:** <description from SKILL.md>
  - **Skill path:** ~/.openclaw/workspace/agents/<agent>/skills/<name>/SKILL.md
  - **Required env:** `VAR1`, `VAR2`
  <!-- sync-skills-end -->
  ```

**No Python source changes.** This task is entirely in `scripts/` and
`RUNBOOK.md`. The quality gate still runs to confirm no regressions.

### Done when

- `./scripts/sync-skills.sh push` copies skill files and upserts the `##
  Skills` block in each agent's TOOLS.md.
- Running push a second time produces no diff in TOOLS.md (idempotent).
- Content outside the sentinel markers is untouched.
- RUNBOOK.md documents the workflow and the manual fallback template.
- Quality gate passes.

---

## Acceptance criteria for Sprint 16

- `PUT /annotations/{id}` returns the full transaction record; no follow-up
  GET required.
- `GET /categories` and `GET /tags` return sorted, deduplicated vocabulary
  arrays from existing annotations.
- `GET /spend?range=last_month` (and the other three shorthands) works without
  explicit dates; resolved dates appear in the response.
- `sync-skills.sh push` idempotently registers skills in each agent's TOOLS.md.
- All quality-gate commands pass on every merged PR.

## Explicitly deferred (out of scope for Sprint 16)

- `GET /spend` filters by `account_id`, `category`, or `tag` (M15 scope;
  depends on BUG-005 account labels).
- `GET /spend/trends` month-over-month endpoint (M16 scope).
- `ledger doctor --fix` auto-remediation (M18 scope).
- Automated TLS provisioning within `ledger serve`.
