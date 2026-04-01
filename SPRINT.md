# Sprint 25 — M23: Remove Annotations Table

## Sprint goal

Eliminate the `annotations` table and every piece of code that reads or writes
it. After this sprint the data model has a single semantic layer: raw financial
events in `transactions`, budgeting data in `allocations`. No production code
will reference `annotations`.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **`annotations` table is dropped, not archived** — All users are verified on
  the latest branch. The M20/M21 double-write backfills have already run on
  every live database. A `DROP TABLE IF EXISTS annotations` migration step is
  sufficient; no safety-net data copy is required.
- **`PUT /annotations/{transaction_id}` is removed entirely** — The primary
  write surface is `PUT /transactions/{id}/allocations`. Skill docs will be
  updated to remove any reference to the annotations endpoint. No compatibility
  shim is left behind.
- **`annotation_write_checklist.md` is merged then deleted** — Review
  `skills/hestia-ledger/checklists/annotation_write_checklist.md` for any
  guidance not already present in `allocation_write_checklist.md`, fold it in,
  then delete the annotation checklist file.
- **Tasks 1 and 2 are the only sequential dependency** — Task 1 removes the
  table, DB functions, and the endpoint all at once (so the quality gate stays
  green at every task boundary). Task 2 cleans up residual test references.
  Task 3 handles all documentation and skill-file updates and can be done
  independently once Task 1 passes.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before the
  next starts.
- No new features, no API shape changes, no schema additions.
- Mark completed tasks `✅ DONE` before committing.

---

## Task 1: Remove `annotations` table, DB layer, and endpoint

### What

This is the core removal task. It must be done atomically so the quality gate
stays green: removing the schema without removing the code (or vice versa)
leaves the test suite broken. All four sub-areas below belong in a single
commit.

### Schema (`src/claw_plaid_ledger/schema.sql`)

Remove the `CREATE TABLE IF NOT EXISTS annotations` block entirely (currently
lines 40–49). New databases will never create the table; the migration step
below handles existing databases.

### Migration (`src/claw_plaid_ledger/db.py` — `initialize_database()`)

1. **Remove** the two annotation backfill patches that currently occupy roughly
   lines 48–106 of `initialize_database()`. Both patches use `LEFT JOIN
   annotations` or subqueries against `annotations`; they will fail once the
   table is dropped, so they must be removed in the same commit.

2. **Add** a new idempotent migration step — positioned after the existing
   column-addition migrations — that drops the table on any live database:

   ```sql
   DROP TABLE IF EXISTS annotations
   ```

   `IF EXISTS` makes this safe for new databases (where the table was never
   created) and for re-runs.

3. **Verify** that `initialize_database()` no longer references `annotations`
   anywhere after these changes.

### DB layer (`src/claw_plaid_ledger/db.py`)

Remove:
- `AnnotationRow` dataclass
- `upsert_annotation()` function
- `get_annotation()` function
- Any imports that are now unused as a result

### API endpoint (`src/claw_plaid_ledger/routers/transactions.py`)

Remove:
- `AnnotationRequest` Pydantic model
- `put_annotation()` route handler (`PUT /annotations/{transaction_id}`)
- Imports of `AnnotationRow` and `upsert_annotation` (now unused)

The module docstring currently reads "Transaction, annotation, and related
endpoints." — update it to remove "annotation".

### Tests

- **Delete** `tests/test_server_annotations.py` in its entirety.
- In `tests/test_db.py`: remove `"annotations"` from the `REQUIRED_TABLES`
  set (line 52), and delete all test functions that exercise `upsert_annotation`,
  `get_annotation`, or the annotation-related backfill behaviour
  (`test_upsert_annotation_*`, `test_get_annotation_*`,
  `test_backfill_insert_copies_annotation_data`,
  `test_backfill_insert_no_annotation_leaves_fields_null`,
  `test_update_migration_restores_annotation_into_null_stub`).

### Doctor schema check

If `ledger doctor` or the preflight module explicitly checks for the presence
of the `annotations` table in the schema, remove that check. The table should
no longer appear in any expected-schema list.

### Done when

- `schema.sql` has no `annotations` definition.
- `initialize_database()` has no reference to `annotations` and includes the
  `DROP TABLE IF EXISTS annotations` step.
- `db.py` exports no annotation-related symbols.
- `PUT /annotations/{transaction_id}` returns HTTP 404 (route does not exist).
- `tests/test_server_annotations.py` is deleted.
- `REQUIRED_TABLES` in `test_db.py` no longer includes `"annotations"`.
- All annotation-specific DB tests are removed.
- Full quality gate passes.

---

## Task 2: Clean up secondary test references

### What

After Task 1, residual annotation references remain in other test files that
seed data or test behaviour adjacent to annotations. This task removes them
cleanly.

### `tests/test_server_categories.py`

This file contains helpers (`_insert_one_annotation_row`, `_seed_annotations`)
that insert rows directly into the `annotations` table to set up category/tag
test data. Because `annotations` no longer exists, replace these helpers with
equivalents that insert rows into `allocations` directly (matching the same
column names: `category`, `tags`, `note`). Update all tests that call these
helpers.

### `tests/test_server_transactions.py`

- Remove `test_after_split_annotations_returns_409` (tests that `PUT
  /annotations/{id}` returns 409 on a split transaction — endpoint is gone).
- Remove or update any remaining tests that call `PUT /annotations/{id}` or
  assert annotation-specific response shapes.

### `search_notes=true` verification

Verify that the `search_notes=true` parameter on `GET /transactions` queries
`allocations.note` (not `annotations.note`) in the SQL produced by the
transactions router. If it still references `annotations`, fix the query to
use `allocations`. The fix must preserve existing test coverage for keyword
search behaviour.

### Done when

- No test file inserts rows into `annotations` or calls `PUT
  /annotations/{id}`.
- `tests/test_server_categories.py` seeds category/tag data via `allocations`.
- `search_notes=true` is confirmed (or corrected) to query `allocations.note`.
- Full quality gate passes.

---

## Task 3: Update documentation and skill files

### What

Sweep all markdown, skill bundles, and proxy config examples to remove or
replace every reference to `annotations`.

### ARCHITECTURE.md

- Remove `PUT /annotations/{id}` from the router listing.
- Remove the `/annotations` auth section entry.
- Remove the `annotations` table from the schema section.
- Update the data-flow description: remove "Hestia annotation pass" or replace
  with equivalent allocation-centric language.
- Remove the double-write paragraph ("decommissioned in M22" is now done).
- Remove all other `annotations`-specific paragraphs or table references.

### README.md

- Remove `PUT /annotations/{id}` from the endpoint list.

### RUNBOOK.md

- Remove operational notes on `PUT /annotations/{transaction_id}` and
  vocabulary management for annotations.
- Update any section that references the `annotations` table in schema or
  migration context.

### Skill files — hestia-ledger

- `skills/hestia-ledger/SKILL.md`: Remove `PUT /annotations/{transaction_id}`
  from the approved API calls list. Remove any language describing it as a
  compatibility shim.
- `skills/hestia-ledger/checklists/annotation_write_checklist.md`: Read the
  file and compare it against `allocation_write_checklist.md`. Fold any
  guidance not already present into `allocation_write_checklist.md`, then
  **delete** `annotation_write_checklist.md`.
- Update any other hestia-ledger file that references the annotations endpoint
  or table.

### Skill files — athena-ledger

- `skills/athena-ledger/SKILL.md`: Remove `PUT /annotations/{transaction_id}`
  from approved API calls. Remove compatibility-shim language.
- Update any other athena-ledger file that references the annotations endpoint
  or table.

### Proxy config examples

Remove `/annotations` path-matching rules from:
- `deploy/proxy/Caddyfile.example`
- `deploy/proxy/nginx-mtls.conf.example`
- `deploy/proxy/authelia-notes.md`

### Docstrings in `routers/accounts.py`

- `GET /categories` docstring currently says "from annotations" — update it to
  say "from allocations".
- `GET /tags` docstring currently says "from annotations" — update it to say
  "from allocations".

### Done when

- `grep -r "annotations" src/ deploy/ skills/ *.md` returns no hits except
  historical references in completed milestone entries in ROADMAP.md and BUGS.md
  (those are intentional historical records and must not be altered).
- Proxy configs contain no `/annotations` route rules.
- Skill bundles reference only `PUT /transactions/{id}/allocations` as the
  write surface.
- Full quality gate passes.

---

## Acceptance criteria for Sprint 25

- No production code (`src/`) references the `annotations` table.
- `PUT /annotations/{transaction_id}` returns HTTP 404 (route removed).
- Transaction categorization, tags, and notes are stored only in `allocations`.
- The schema is simpler: `transactions` holds raw financial events;
  `allocations` holds all semantic/budgeting data.
- `DROP TABLE IF EXISTS annotations` runs idempotently on every startup,
  cleaning up any live database that still carries the old table.
- All annotation backfill/migration patches are removed from
  `initialize_database()`.
- Full quality gate (`ruff format`, `ruff check`, `mypy`, `pytest`) passes with
  no regressions.
