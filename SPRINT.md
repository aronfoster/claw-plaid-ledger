# Sprint 22 — M20: Allocation Model for Multi-Purpose Transactions

## Sprint goal

Introduce `allocations` as the budgeting layer. Every Plaid transaction maps
to exactly one allocation row in this sprint (multi-allocation editing and
management come in M21). Spend reporting, vocabulary discovery, and all
transaction views switch to reading from `allocations` as the sole source of
budgeting truth. The `annotations` table continues to exist and receive
double-writes through this sprint; it is decommissioned in M23.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **Allocation schema** — `id`, `plaid_transaction_id`, `amount`, `category`,
  `tags` (JSON array, same encoding as `annotations.tags`), `note`,
  `created_at`, `updated_at`. No UNIQUE constraint on `plaid_transaction_id`
  so M21 can add multiple rows per transaction.
- **Every transaction gets an allocation row** — unannotated transactions get
  an allocation with `amount = transaction.amount` and all semantic fields
  null. This ensures LEFT JOINs never drop transactions from results.
- **Double-write in M20** — `PUT /annotations/{id}` continues to write
  `category`, `tags`, and `note` to `annotations` AND upserts a single
  allocation row. The `annotations` table is not removed until M23.
- **Response shape** — the `annotation` key on transaction responses is
  replaced by an `allocation` key. The `allocation` object contains `id`,
  `amount`, `category`, `tags`, `note`, `updated_at`.
- **Spend uses allocation amounts** — `GET /spend` and `GET /spend/trends`
  sum `allocations.amount`, not `transactions.amount`. For M20 (1:1), totals
  are numerically identical; the change future-proofs multi-allocation math.
- **One list row per allocation** — `GET /transactions` returns one row per
  allocation. For M20, this is identical to one row per transaction.
  In M21+, a transaction with multiple allocations will appear multiple times.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before
  the next starts.
- Zero sync/import ownership changes — Plaid transaction rows stay immutable.
- Mark completed tasks `✅ DONE` before committing.
- Apply `_strict_params` to any new parameterised GET endpoint
  (per the Sprint 21 deferred note).

---

## Task 1: Allocations schema, DB layer, and transaction seeding ✅ DONE

### What

Introduce the `allocations` table and all DB-layer primitives that the
remaining tasks depend on. Also ensure every transaction always has an
allocation row via two complementary mechanisms.

### Schema addition (`schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS allocations (
    id           INTEGER PRIMARY KEY,
    plaid_transaction_id TEXT NOT NULL
        REFERENCES transactions(plaid_transaction_id),
    amount       NUMERIC NOT NULL,
    category     TEXT,
    tags         TEXT,
    note         TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

Note: no UNIQUE constraint on `plaid_transaction_id` — a future sprint will
add multiple allocations per transaction.

### DB layer (`db.py`)

Add:

- **`AllocationRow`** dataclass — fields: `id: int | None`, `plaid_transaction_id: str`,
  `amount: float`, `category: str | None`, `tags: str | None`,
  `note: str | None`, `created_at: str`, `updated_at: str`.
- **`upsert_single_allocation(connection, row: AllocationRow) -> int`** —
  updates the first/only allocation for `plaid_transaction_id` if one exists,
  otherwise inserts. Returns the allocation `id`. This is the write surface
  for M20's 1:1 case; M21 will add separate functions for multi-allocation
  management.
- **`get_allocations_for_transaction(connection, plaid_transaction_id: str) -> list[AllocationRow]`** —
  returns all allocation rows for the transaction, ordered by `id ASC`.
  Returns an empty list if none exist.

### Seeding mechanism 1 — new transactions

Update **`upsert_transaction()`** in `db.py` to also execute an
`INSERT OR IGNORE INTO allocations ...` immediately after the transaction
upsert. This creates a blank allocation (amount = transaction amount, all
semantic fields NULL) for every new transaction that doesn't yet have one.
Existing allocations are not touched (the `OR IGNORE` handles idempotency
via the rowid primary key — if any allocation already exists for the
transaction, the insert is skipped via a pre-check, not a UNIQUE constraint).

Implementation note: because there is no UNIQUE constraint on
`plaid_transaction_id`, raw `INSERT OR IGNORE` cannot be used. Instead,
insert only when `NOT EXISTS (SELECT 1 FROM allocations WHERE plaid_transaction_id = ?)`.

### Seeding mechanism 2 — startup backfill

Add an idempotent migration in **`initialize_database()`** (alongside the
existing `ALTER TABLE` migrations) that backfills an allocation for every
transaction that has no allocation row yet:

```sql
INSERT INTO allocations (plaid_transaction_id, amount, created_at, updated_at)
SELECT t.plaid_transaction_id, t.amount, ?, ?
FROM transactions t
WHERE NOT EXISTS (
    SELECT 1 FROM allocations a
    WHERE a.plaid_transaction_id = t.plaid_transaction_id
)
```

This runs once per startup. It catches transactions that were synced before
the `allocations` table existed and any transactions synced between
`upsert_transaction` and a restart.

### Tests

- `AllocationRow` construction and default values.
- `upsert_single_allocation` — insert path (no prior row) and update path
  (existing row).
- `get_allocations_for_transaction` — empty, one row, ordered.
- `upsert_transaction` now creates a blank allocation alongside the
  transaction (verify with `get_allocations_for_transaction`).
- Startup backfill is idempotent (runs twice; count stays the same).

### Done when

- `allocations` table is in `schema.sql`.
- `AllocationRow`, `upsert_single_allocation`, `get_allocations_for_transaction`
  are in `db.py`.
- `upsert_transaction` creates a blank allocation for new transactions.
- Startup migration backfills missing allocations.
- Quality gate passes.

---

## Task 2: PUT /annotations double-write; transaction detail response shape ✅ DONE

### What

`PUT /annotations/{id}` writes to both `annotations` (as today) and
`allocations` (as the new budgeting layer). The transaction detail and
annotation-write responses replace the `annotation` key with `allocation`.

### `PUT /annotations/{id}` (`routers/transactions.py`)

After writing to `annotations` (unchanged), also call
`upsert_single_allocation()` with:

- `plaid_transaction_id` = transaction id
- `amount` = the transaction's `amount` (fetch from `get_transaction()`, which
  is already called to verify existence)
- `category` = `body.category`
- `tags` = `body.tags` serialised to JSON (same encoding as annotations)
- `note` = `body.note`
- `created_at` / `updated_at` = same timestamps used for the annotation

The request body shape (`AnnotationRequest`) does **not** change — it still
accepts `category`, `note`, and `tags`.

### Transaction detail helper (`routers/transactions.py` and `db.py`)

Rename `_fetch_transaction_with_annotation()` to
`_fetch_transaction_with_allocation()`. Update it to:

1. Call `get_transaction()` as before.
2. Call `get_allocations_for_transaction()` and take the first row (if any).
3. Build the `allocation` payload from that row:
   ```python
   {
       "id": alloc.id,
       "amount": alloc.amount,
       "category": alloc.category,
       "note": alloc.note,
       "tags": json.loads(alloc.tags) if alloc.tags else None,
       "updated_at": alloc.updated_at,
   }
   ```
4. Return `{**transaction, "allocation": allocation_payload}`.

Remove all references to `get_annotation()` and `AnnotationRow` from
`routers/transactions.py`. Those imports are no longer needed in the router
(the DB-level annotation write still uses `upsert_annotation` from `db.py`).

The response from both `GET /transactions/{id}` and `PUT /annotations/{id}`
now contains `allocation` instead of `annotation`.

### Tests

- `PUT /annotations/{id}` — verify the allocation row is created/updated with
  the correct `amount`, `category`, `tags`, and `note`.
- `GET /transactions/{id}` — verify the response has an `allocation` key and
  no `annotation` key.
- `PUT /annotations/{id}` — verify response has `allocation` key (no follow-up
  GET needed).
- Existing annotation round-trip tests: update assertions to use `allocation.*`
  field paths instead of `annotation.*`.

### Done when

- `PUT /annotations/{id}` writes to both tables.
- `GET /transactions/{id}` and `PUT /annotations/{id}` return `allocation`
  in their response, not `annotation`.
- Quality gate passes.

---

## Task 3: GET /transactions list joins allocations ✅ DONE

### What

`query_transactions()` in `db.py` switches its LEFT JOIN from `annotations`
to `allocations`. Each result row contains `allocation` data (same shape as
Task 2's detail response). Pagination counts allocations, not annotation rows
(in M20, identical; M21+ will see the difference for multi-allocation
transactions).

### `query_transactions()` (`db.py`)

Replace:
```python
annotations_join = (
    "LEFT JOIN annotations ann "
    "ON ann.plaid_transaction_id = t.plaid_transaction_id "
)
```
with:
```python
allocations_join = (
    "LEFT JOIN allocations alloc "
    "ON alloc.plaid_transaction_id = t.plaid_transaction_id "
)
```

Update the SELECT projection to use `alloc.*` columns instead of `ann.*`.
Build the `allocation` dict in the result rows using the same shape as
Task 2's detail response (id, amount, category, tags, note, updated_at).

The tag filter (`_apply_tag_filters`) currently checks `ann.tags`; update to
`alloc.tags`.

The `search_notes` keyword filter (`ann.note LIKE ?`) updates to `alloc.note LIKE ?`.

Remove `_annotation_from_joined_row()` and replace with an equivalent
`_allocation_from_joined_row()` helper.

### `TransactionQuery` (`db.py`)

No field changes needed. The `tags` and `search_notes` filters work the same
way; only the column reference changes.

### `_TRANSACTIONS_ALLOWED_PARAMS` (`routers/transactions.py`)

No change required — the `tags` filter continues to work; it now targets
`alloc.tags`.

### Tests

- List result rows contain `allocation` key (not `annotation`).
- Tag filter works against allocation tags.
- `search_notes=true` keyword filter works against `alloc.note`.
- Pagination `total` is correct (matches allocation count, same as transaction
  count for M20).
- Existing list-endpoint tests: update assertions from `annotation.*` to
  `allocation.*`.

### Done when

- `query_transactions()` JOINs `allocations`, not `annotations`.
- List rows contain `allocation`, not `annotation`.
- All tag and note-search filters work correctly.
- Quality gate passes.

---

## Task 4: Spend, trends, categories, and tags switch to allocations ✅ DONE

### What

All remaining read paths that currently JOIN `annotations` are updated to
JOIN `allocations`. Spend totals use `allocations.amount`.

### `query_spend()` and `query_spend_trends()` (`db.py`)

Replace `LEFT JOIN annotations ann` with `LEFT JOIN allocations alloc` in
both functions.

Replace `SUM(t.amount)` with `SUM(alloc.amount)` in `query_spend()`.
Replace `COALESCE(SUM(t.amount), 0.0)` with `COALESCE(SUM(alloc.amount), 0.0)`
in `query_spend_trends()`.

Category filter: `LOWER(ann.category) = LOWER(?)` → `LOWER(alloc.category) = LOWER(?)`.

Tag filters (`EXISTS (SELECT 1 FROM json_each(ann.tags) ...)`) → change
column reference to `alloc.tags`.

`transaction_count` in the spend and trends responses is semantically
"allocation count" after this change, but the field name is preserved for
backward compatibility. (A rename can be done in M21 alongside multi-allocation
management if desired.)

### `get_distinct_categories()` and `get_distinct_tags()` (`db.py`)

Update both functions to query `allocations` instead of `annotations`:

```python
# categories
"SELECT DISTINCT category FROM allocations "
"WHERE category IS NOT NULL ORDER BY category COLLATE NOCASE"

# tags
"SELECT DISTINCT j.value "
"FROM allocations a, json_each(a.tags) j "
"WHERE a.tags IS NOT NULL ORDER BY j.value COLLATE NOCASE"
```

No changes required in `routers/spend.py`, `routers/accounts.py`, or the
`SpendQuery` / `SpendTrendsQuery` dataclasses — the router layer is unaffected.

### Tests

- `GET /spend` — totals match allocation amounts (in M20, same as transaction
  amounts; write a test that verifies the JOIN is on allocations, e.g. by
  creating a transaction + allocation with a different amount and confirming
  spend reflects the allocation amount).
- `GET /spend?category=...` — filters against allocation category.
- `GET /spend/trends` — monthly buckets use allocation amounts.
- `GET /categories` — returns distinct values from `allocations.category`.
- `GET /tags` — returns distinct values from `allocations.tags`.
- Existing spend and trends tests: verify no regressions; update category/tag
  fixture setup to write to `allocations` (not `annotations`) where relevant.

### Done when

- All spend/trends queries JOIN `allocations` and sum `alloc.amount`.
- `GET /categories` and `GET /tags` read from `allocations`.
- Quality gate passes.

---

## Task 5: Skill bundle updates

### What

Both `skills/hestia-ledger/` and `skills/athena-ledger/` are updated to
reflect the allocation model. Agents reading stale skill docs would otherwise
reference `annotation.category` / `annotation.tags` fields that no longer
exist in the response.

### Changes required across both skill bundles

**In `SKILL.md` and any checklist/playbook that references annotations as a
category/tag store:**

- Replace all references to `annotation.category`, `annotation.tags`, and
  `annotation.note` with `allocation.category`, `allocation.tags`,
  `allocation.note`.
- Document the `allocation` object shape returned by `GET /transactions` and
  `GET /transactions/{id}`:
  ```json
  "allocation": {
    "id": 1,
    "amount": 50.00,
    "category": "groceries",
    "tags": ["household"],
    "note": "weekly shopping",
    "updated_at": "2026-03-25T10:00:00+00:00"
  }
  ```
- Note that `allocation` is always present on a transaction response
  (never null); `category`, `tags`, and `note` within it may be null for
  uncategorized transactions.
- Note that `PUT /annotations/{id}` continues to be the write surface for
  category, tags, and note. Its request body is unchanged
  (`{ "category": ..., "tags": [...], "note": ... }`). The response now
  contains `allocation` instead of `annotation`.

**In Hestia's `SKILL.md` (ingestion loop):**

- Update the "Deterministic ingestion loop" step 2 to read:
  "Each row includes a nested `allocation` field. Use `allocation.category`,
  `allocation.tags`, and `allocation.note` to screen for missing or stale
  categorization."
- Update the "Drill-down before annotation write" playbook to reference
  `allocation.*` fields.

**In Hestia's `checklists/annotation_write_checklist.md` and
`checklists/query_playbooks.md`:**

- Update field references as above.

**In Athena's `SKILL.md` (vocabulary discovery, spend rollups):**

- Vocabulary discovery section: `GET /categories` and `GET /tags` now draw
  from `allocations`; agent behavior is unchanged (same API call), but the
  note that these reflect "annotation vocabulary" should be updated to
  "allocation vocabulary".
- Spend rollup section: note that `GET /spend` sums allocation amounts.
  For M20, this is numerically identical to transaction amounts. In future
  sprints, a multi-allocation transaction will contribute only its matching
  allocation amount when filtered by category.

**In Athena's `checklists/query_playbooks.md`:**

- Update all playbook entries that reference `annotation.*` fields.

### Done when

- No skill file references `annotation.category`, `annotation.tags`, or
  `annotation.note`.
- Both `SKILL.md` files document the `allocation` object shape.
- `PUT /annotations/{id}` request contract is clearly documented as unchanged.
- Quality gate passes.

---

## Acceptance criteria for Sprint 22

- `allocations` table exists and is populated for every transaction on
  startup.
- `upsert_transaction()` seeds a blank allocation for every new sync.
- `PUT /annotations/{id}` writes to both `annotations` and `allocations`.
- All transaction responses (`GET /transactions`, `GET /transactions/{id}`,
  `PUT /annotations/{id}`) carry `allocation` (not `annotation`).
- `GET /spend`, `GET /spend/trends` sum `allocations.amount`.
- `GET /categories` and `GET /tags` draw vocabulary from `allocations`.
- Existing categorized transactions surface their category/tags through the
  `allocation` object after migration.
- Plaid sync logic is unchanged; `transactions` table remains immutable.
- Full quality gate passes with no regressions.

## Explicitly deferred

- Multi-allocation creation, editing, and deletion (M21).
- Enforcement of the sum-equals-transaction-amount invariant (M21).
- Removal of `annotations` table (M23).
- Renaming `transaction_count` to `allocation_count` in spend responses
  (revisit in M21 alongside multi-allocation work).
- Adding a `category` filter to `GET /transactions` (low-priority; revisit
  after M21 when the allocation model stabilizes).
