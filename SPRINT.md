# Sprint 27 — Batch Allocation Updates & Uncategorized Transaction Query (M24)

## Sprint goal

Give Hestia a single batch-update command per ingestion run (replacing N
individual PUT calls) and give both agents dedicated query endpoints for
uncategorized and split transactions — eliminating client-side filtering.

## Background

Today, Hestia's ingestion loop pages through `GET /transactions`, identifies
uncategorized rows client-side, then issues one
`PUT /transactions/{id}/allocations` per transaction. For a typical run of
30–50 updates this is noisy and slow. The batch endpoint collapses that into
a single POST.

Athena currently identifies split transactions by scanning list results for
duplicate transaction IDs. A dedicated `/transactions/splits` endpoint removes
that heuristic. Similarly, `/transactions/uncategorized` gives Hestia a
pre-filtered work queue.

Amount-range filters (`min_amount`, `max_amount`) and keyword search against
`name`/`merchant_name` already exist on `GET /transactions`. Those M24 items
are already delivered and are **not** included in this sprint.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **`GET /transactions/uncategorized` returns one row per uncategorized
  allocation.** The endpoint uses the same transaction+allocation JOIN as
  `GET /transactions` and adds `WHERE alloc.category IS NULL`. A split
  transaction with two allocations — one categorized, one not — returns only
  the uncategorized allocation row. Response shape is identical to
  `GET /transactions`.

- **`GET /transactions/splits` returns one row per allocation for split
  transactions.** A split transaction is one with more than one allocation.
  All allocations of qualifying transactions are returned (not just
  uncategorized ones). Response shape is identical to `GET /transactions`.

- **Both new endpoints accept the full `GET /transactions` filter set** —
  `start_date`, `end_date`, `range`, `account_id`, `pending`, `min_amount`,
  `max_amount`, `keyword`, `view`, `limit`, `offset`, `search_notes`, `tags`.
  Parameter validation uses the same `_strict_params` mechanism.

- **`POST /transactions/allocations/batch` uses collect-all-errors semantics.**
  Every item in the batch is processed independently. Failures do not abort
  the remaining items. Each item commits independently (not atomic across the
  batch).

- **Batch request items use replace semantics for semantic fields.** Omitted
  fields (`category`, `tags`, `note`) are set to NULL — not preserved from
  the existing allocation. The `amount` is not included in the request; it
  remains equal to the transaction amount (single-allocation invariant).

- **Batch response is a summary, not full records:**
  ```json
  {
    "succeeded": ["txn_id_1", "txn_id_2"],
    "failed": [
      {"transaction_id": "txn_id_3", "error": "transaction not found"},
      {"transaction_id": "txn_id_4", "error": "split transaction (2 allocations); use PUT /transactions/{id}/allocations"}
    ]
  }
  ```

- **Batch endpoint rejects split transactions.** If a transaction has more
  than one allocation, it goes into the `failed` array with a message
  directing the caller to `PUT /transactions/{id}/allocations`.

- **Merchant text search is not included.** The existing `keyword` parameter
  already searches `name` and `merchant_name`. Dropped from M24 scope.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before
  the next starts.
- Mark completed tasks `✅ DONE` before committing.

---

## Task 1: `GET /transactions/uncategorized`

### What

Add a new endpoint that returns the subset of transaction+allocation rows
where the allocation has no category. This gives Hestia a focused work queue
without client-side filtering.

### Database layer (`db.py`)

Add a `uncategorized_only: bool = False` field to `TransactionQuery`.

When `uncategorized_only` is `True`, add `alloc.category IS NULL` to the
WHERE clause in `query_transactions()`. No other changes to the query
function are needed — the allocation LEFT JOIN is already in place.

### Router (`routers/transactions.py`)

Add a new endpoint:

```
GET /transactions/uncategorized
```

- **Accepted query parameters:** identical to `GET /transactions`
  (`start_date`, `end_date`, `range`, `account_id`, `pending`, `min_amount`,
  `max_amount`, `keyword`, `view`, `limit`, `offset`, `search_notes`, `tags`).
- **`_strict_params`:** use the same `_TRANSACTIONS_ALLOWED_PARAMS` frozenset.
- **Auth:** `require_bearer_token` dependency.
- **Implementation:** reuse the existing `list_transactions` logic (or factor
  out a shared helper) with `uncategorized_only=True` on the
  `TransactionQuery`.
- **Response shape:** identical to `GET /transactions`:
  ```json
  {
    "transactions": [...],
    "total": <int>,
    "limit": <int>,
    "offset": <int>
  }
  ```
  Each row contains a singular `"allocation"` object (same as list view).

### Tests

Add tests to the appropriate `test_server_transactions*.py` file:

- Seed two transactions: one with a categorized allocation, one without.
  Confirm the uncategorized endpoint returns only the uncategorized one.
- Seed a split transaction (2 allocations: one categorized, one not).
  Confirm only the uncategorized allocation row is returned.
- Seed a split transaction where both allocations have categories. Confirm
  it does NOT appear in results.
- Confirm date range, pagination, and at least one other filter
  (e.g. `account_id`) work correctly.
- Confirm `_strict_params` rejects unknown parameters with HTTP 422.

### Done when

- `GET /transactions/uncategorized` returns only rows where
  `alloc.category IS NULL`.
- Split transactions with mixed categorization show only their uncategorized
  allocation rows.
- Full `GET /transactions` filter set works.
- Full quality gate passes.

---

## Task 2: `GET /transactions/splits`

### What

Add a new endpoint that returns all allocations belonging to split
transactions (those with more than one allocation). This gives Athena a
dedicated review queue for split transactions.

### Database layer (`db.py`)

Add a `splits_only: bool = False` field to `TransactionQuery`.

When `splits_only` is `True`, restrict results to transactions that have
more than one allocation. The recommended approach is a subquery:

```sql
t.plaid_transaction_id IN (
    SELECT plaid_transaction_id FROM allocations
    GROUP BY plaid_transaction_id HAVING COUNT(*) > 1
)
```

All allocations of qualifying transactions are returned (not just some).
The existing LEFT JOIN on allocations provides one row per allocation, so a
split transaction with 3 allocations produces 3 result rows.

### Router (`routers/transactions.py`)

Add a new endpoint:

```
GET /transactions/splits
```

- **Accepted query parameters:** identical to `GET /transactions`.
- **`_strict_params`:** same `_TRANSACTIONS_ALLOWED_PARAMS` frozenset.
- **Auth:** `require_bearer_token` dependency.
- **Implementation:** reuse the existing query infrastructure with
  `splits_only=True`.
- **Response shape:** identical to `GET /transactions`.

### Tests

- Seed one single-allocation transaction and one split (2 allocations).
  Confirm only the split's rows are returned, and both allocation rows
  appear.
- Confirm `total` reflects the allocation-row count (not the transaction
  count).
- Confirm date range filters and pagination work.
- Confirm `_strict_params` rejects unknown parameters with HTTP 422.

### Done when

- `GET /transactions/splits` returns only rows for transactions with
  multiple allocations.
- All allocations of qualifying transactions appear in results.
- Response shape matches `GET /transactions`.
- Full quality gate passes.

---

## Task 3: `POST /transactions/allocations/batch`

### What

Add a batch endpoint that accepts an array of simple allocation updates
for single-allocation transactions. This replaces Hestia's per-transaction
PUT loop with a single request.

### Request shape

```json
[
  {
    "transaction_id": "abc123",
    "category": "groceries",
    "tags": ["household", "recurring"],
    "note": "weekly Costco run"
  },
  {
    "transaction_id": "def456",
    "category": "software"
  }
]
```

**Request model** (Pydantic):

```python
class BatchAllocationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    category: str | None = None
    tags: list[str] | None = None
    note: str | None = None
```

- `transaction_id` is required.
- `category`, `tags`, `note` are optional. **Omitted fields are set to NULL**
  (replace semantics). This means sending `{"transaction_id": "x",
  "category": "food"}` clears any existing `tags` and `note` on that
  allocation.
- `amount` is NOT in the request — it stays equal to the transaction amount.
- The body is a JSON array of `BatchAllocationItem` (not wrapped in an
  object). Reject an empty array with HTTP 422.

### Response shape

```json
{
  "succeeded": ["abc123", "def456"],
  "failed": [
    {
      "transaction_id": "ghi789",
      "error": "transaction not found"
    },
    {
      "transaction_id": "jkl012",
      "error": "split transaction (2 allocations); use PUT /transactions/{id}/allocations"
    }
  ]
}
```

HTTP status: **200** always (even if some items failed). The caller inspects
`failed` to determine per-item outcomes.

### Processing rules (per item, independent commits)

For each item in the array:

1. **Look up the transaction.** If not found → add to `failed` with
   `"transaction not found"`.
2. **Fetch allocations.** If allocation count != 1 → add to `failed` with
   `"split transaction (N allocations); use PUT /transactions/{id}/allocations"`.
3. **Update the single allocation** — set `category`, `tags`, `note` to the
   request values (NULL for omitted fields). `amount` stays unchanged.
   `updated_at` is set to now. Do NOT use `replace_allocations` (that deletes
   and reinserts, which is overkill for a field update). A simple UPDATE is
   sufficient:
   ```sql
   UPDATE allocations
   SET category = ?, tags = ?, note = ?, updated_at = ?
   WHERE plaid_transaction_id = ?
   ```
4. Add to `succeeded`.

Each item should commit independently. The simplest approach: process all
items within a single `sqlite3.connect()` context, but call
`connection.commit()` after each successful update (or use autocommit). If
independent commits add undue complexity, an atomic approach (single
transaction, fail-fast on first error) is acceptable as a fallback — but
document the change from the spec.

### Router (`routers/transactions.py`)

```
POST /transactions/allocations/batch
```

- **Auth:** `require_bearer_token` dependency.
- **No query parameters** — no `_strict_params` needed.
- **Request body:** `list[BatchAllocationItem]`.
- **Response:** `{"succeeded": [...], "failed": [...]}`.

### Tests

- **Happy path:** batch of 3 valid single-allocation transactions. All appear
  in `succeeded`.
- **Mixed results:** batch with one valid, one not-found, one split. Confirm
  correct placement in `succeeded` / `failed`.
- **Replace semantics:** send an update with only `category` set. Confirm
  that `tags` and `note` are NULL afterward (not preserved from prior state).
- **Empty array:** returns HTTP 422.
- **Split rejection:** transaction with 2 allocations → appears in `failed`
  with message mentioning the allocation count and the alternative endpoint.

### Done when

- `POST /transactions/allocations/batch` processes each item independently.
- Single-allocation transactions are updated; splits and not-found are
  reported in `failed`.
- Replace semantics: omitted fields become NULL.
- Full quality gate passes.

---

## Task 4: Update skill bundles

### What

Update both `skills/hestia-ledger/SKILL.md` and
`skills/athena-ledger/SKILL.md` with the three new endpoints.

### Hestia skill updates

Hestia is the primary consumer of `/transactions/uncategorized` and
`/transactions/allocations/batch`.

**Approved API calls section:** add:

- `GET /transactions/uncategorized` — pre-filtered work queue; returns only
  allocations with null category. Supports all `GET /transactions` filters.
- `POST /transactions/allocations/batch` — batch-update allocations for
  single-allocation transactions. Replaces the per-transaction PUT loop.

**Making API calls section:** add examples:

```bash
# Uncategorized work queue
ledger-api "/transactions/uncategorized?range=last_30_days&view=canonical"

# Batch allocation update
ledger-api /transactions/allocations/batch \
  -X POST -H "Content-Type: application/json" \
  -d '[
    {"transaction_id": "abc123", "category": "groceries", "tags": ["household"], "note": "weekly shop"},
    {"transaction_id": "def456", "category": "utilities", "tags": ["recurring"]}
  ]'
```

**Ingestion loop guidance:** update to reflect the new workflow:

1. Query `GET /transactions/uncategorized?range=last_30_days` (replaces the
   full `GET /transactions` + client-side null-category filter).
2. For each uncategorized allocation, determine category/tags/note.
3. Re-fetch split candidates with `GET /transactions/{id}` if the same
   transaction ID appears multiple times in results (it's a split with
   multiple uncategorized allocations).
4. Collect all single-allocation updates into a batch array.
5. POST to `/transactions/allocations/batch`.
6. Inspect `failed` array — log or escalate any failures.
7. For splits: continue to use `PUT /transactions/{id}/allocations`
   individually.

**Batch replace-semantics warning:** Add a clear note in the skill:

> **Batch updates use replace semantics.** Every field you omit is set to
> NULL. If a transaction already has `tags: ["recurring"]` and you send
> `{"transaction_id": "x", "category": "utilities"}`, the tags will be
> cleared. Always include all fields you want to keep.

### Athena skill updates

Athena is the primary consumer of `/transactions/splits`.

**Approved API calls section:** add:

- `GET /transactions/uncategorized` — view uncategorized allocations across
  the ledger. Supports all `GET /transactions` filters.
- `GET /transactions/splits` — dedicated review queue for split transactions.
  Returns all allocations for transactions with multiple allocations.
- `POST /transactions/allocations/batch` — available but rarely needed;
  prefer `PUT /transactions/{id}/allocations` for analyst-level corrections.

**Query playbooks** (`checklists/query_playbooks.md`): add entries for:

- "Uncategorized review" — `GET /transactions/uncategorized?range=last_30_days`
- "Split transaction review" — `GET /transactions/splits?range=last_30_days`

### Done when

- Both SKILL.md files list all three new endpoints in their approved API
  calls sections.
- Hestia's skill includes batch usage examples, replace-semantics warning,
  and updated ingestion loop guidance.
- Athena's skill includes splits and uncategorized query playbooks.
- `grep -r "allocations/batch" skills/` returns hits in both skill files.
- Full quality gate passes.

---

## Acceptance criteria for Sprint 27

- `GET /transactions/uncategorized` returns only allocation rows where
  `category IS NULL`, including uncategorized allocations of split
  transactions.
- `GET /transactions/splits` returns all allocation rows for transactions
  with multiple allocations.
- Both new GET endpoints accept the full `GET /transactions` filter and
  pagination set.
- `POST /transactions/allocations/batch` accepts a JSON array of
  `{transaction_id, category?, tags?, note?}` items and returns a
  `{succeeded, failed}` summary.
- Batch updates use replace semantics: omitted fields become NULL.
- Batch rejects split transactions with a clear error message.
- Both skill bundles updated with all new endpoints and guidance.
- Full quality gate (`ruff format`, `ruff check`, `mypy`, `pytest`) passes
  with no regressions.
