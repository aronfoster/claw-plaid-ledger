# Sprint 23 — M21: Manual Allocation Editing

## Sprint goal

Make multi-allocation transactions fully usable. An operator can split one
imported transaction across multiple categories, with the system enforcing
that allocation amounts always reconcile to the transaction total. Unmodified
(single-allocation) transactions continue to work exactly as before.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **Response shape — breaking change** — all transaction responses change from
  `"allocation": {...}` (single object) to `"allocations": [...]` (array). The
  array is always present and never null. List rows (`GET /transactions`) always
  carry a single-element array (one list row = one allocation). Detail views
  (`GET /transactions/{id}`, `PUT /annotations/{id}`, and the new
  `PUT /transactions/{id}/allocations`) carry all allocations.
- **Replace-all API only** — `PUT /transactions/{id}/allocations` accepts an
  array and atomically replaces all existing allocations. No individual
  create/patch/delete sub-operations in this sprint.
- **Amount validation with auto-correction** — amounts are compared after
  rounding to 2 decimal places. If the difference between `sum(allocation amounts)`
  and `transaction.amount` is ≤ $1.00, the server silently adjusts the last
  allocation to balance exactly. If the difference is > $1.00, the server
  returns HTTP 422 with `transaction_amount`, `allocation_total`, and
  `difference` in the error body.
- **`PUT /annotations/{id}` restriction** — if the transaction has more than one
  allocation, this endpoint returns HTTP 409 directing the caller to use
  `PUT /transactions/{id}/allocations` instead. Single-allocation transactions
  continue to work as before.
- **`transaction_count` → `allocation_count`** — the spend and spend/trends
  response field is renamed (deferred from Sprint 22). This is a breaking change
  on `GET /spend` and `GET /spend/trends`.
- **CLI** — `ledger allocations show <transaction_id>` and
  `ledger allocations set <transaction_id> --file <path>` are both required.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before the
  next starts.
- Zero sync/import ownership changes — `transactions` table remains immutable.
- Mark completed tasks `✅ DONE` before committing.
- Apply `_strict_params` to any new parameterised GET endpoint.

---

## Task 1: DB layer — `replace_allocations` and all-allocation fetching

### What

Add the `replace_allocations()` primitive, update the transaction-detail helper
to return all allocations (not just the first), and verify that
`query_transactions()` pagination counts allocation rows.

### `replace_allocations` (`db.py`)

```python
def replace_allocations(
    connection: sqlite3.Connection,
    plaid_transaction_id: str,
    rows: list[AllocationRow],
) -> list[int]:
    ...
```

- Execute inside a single `BEGIN`/`COMMIT` block.
- `DELETE FROM allocations WHERE plaid_transaction_id = ?`
- INSERT each row in order; collect and return the list of inserted `id` values.
- The caller is responsible for validation (non-empty list, amounts balanced).
- Raise `ValueError` if `rows` is empty (guard against accidental data loss).

### `_fetch_transaction_with_allocations` (`routers/transactions.py`)

Rename `_fetch_transaction_with_allocation()` (singular) to
`_fetch_transaction_with_allocations()` (plural). Update it to build the full
list instead of taking only the first element:

```python
allocations_payload = [
    {
        "id": alloc.id,
        "amount": alloc.amount,
        "category": alloc.category,
        "note": alloc.note,
        "tags": json.loads(alloc.tags) if alloc.tags else None,
        "updated_at": alloc.updated_at,
    }
    for alloc in allocs   # allocs = get_allocations_for_transaction(...)
]
return {**transaction, "allocations": allocations_payload}
```

### Pagination in `query_transactions()` (`db.py`)

`total` in the list response must count allocation rows, not transaction rows.
With `LEFT JOIN allocations`, a transaction split into N allocations produces N
result rows — `total` must reflect this so callers can paginate correctly.

Verify the `SELECT COUNT(*)` sub-query used for `total` includes the same
`LEFT JOIN allocations` and produces the same row count as the main SELECT.
Fix if it currently counts only transactions.

### Tests

- `replace_allocations` — insert path: no prior rows, returns correct IDs.
- `replace_allocations` — replace path: 2 prior allocations → replace with 3 →
  count is 3, old IDs absent.
- `replace_allocations` raises `ValueError` for empty list.
- `_fetch_transaction_with_allocations` returns all rows ordered by `id ASC`.
- `query_transactions` — create a transaction with 2 allocations; verify `total`
  is 2 and both rows appear.

### Done when

- `replace_allocations` is in `db.py`.
- `_fetch_transaction_with_allocations()` returns `"allocations": [...]` (all).
- Pagination `total` reflects allocation-row count.
- Quality gate passes.

---

## Task 2: Response shape — `allocation` → `allocations`; spend field rename

### What

Breaking change across all transaction and spend endpoints. No new DB writes;
pure response-shape and field-name updates.

### Transaction list (`db.py` — `_allocation_from_joined_row`)

Rename `_allocation_from_joined_row` to `_allocation_list_from_joined_row` (or
similar). Update it to return the allocation wrapped in a single-element list:

```python
# Old shape per list row:
"allocation": {"id": 5, "amount": ..., ...}

# New shape per list row:
"allocations": [{"id": 5, "amount": ..., ...}]
```

The allocation object fields (id, amount, category, tags, note, updated_at)
are unchanged.

### Transaction detail (`routers/transactions.py`)

- `GET /transactions/{id}`: call `_fetch_transaction_with_allocations()` (Task 1).
  Response now has `"allocations": [...]`.
- `PUT /annotations/{id}`:
  - After verifying the transaction exists, count its allocations:
    ```python
    allocs = get_allocations_for_transaction(connection, transaction_id)
    if len(allocs) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "transaction has multiple allocations",
                "message": (
                    "Use PUT /transactions/{id}/allocations "
                    "to edit split transactions."
                ),
                "allocation_count": len(allocs),
            },
        )
    ```
  - Replace the `_fetch_transaction_with_allocation()` call in the response with
    `_fetch_transaction_with_allocations()`. Response now has `"allocations": [...]`.

### Spend field rename (`routers/spend.py`, `db.py`)

In `query_spend()` and `query_spend_trends()`, rename the response field:

```python
# Old:
"transaction_count": row["count"]

# New:
"allocation_count": row["count"]
```

Update the field name in both the `SpendResult` / `SpendTrendBucket` response
models (or dicts) and in both router handlers. The underlying SQL `COUNT(*)`
is unchanged — only the JSON key name changes.

### Tests

- `GET /transactions/{id}` — response has `"allocations"` key (array), not
  `"allocation"`.
- `GET /transactions` list — each row has `"allocations"` key (single-element
  array for the default 1:1 case).
- `PUT /annotations/{id}` — response has `"allocations"` key.
- `PUT /annotations/{id}` — returns HTTP 409 when the transaction has 2
  allocations; response body contains `"allocation_count": 2`.
- `GET /spend` — response has `"allocation_count"`, not `"transaction_count"`.
- `GET /spend/trends` — each bucket has `"allocation_count"`.
- Update **all** existing tests that check `response["allocation"]` or
  `result["transaction_count"]` to use the new key names.

### Done when

- No response body anywhere contains `"allocation"` (singular object key) or
  `"transaction_count"`.
- `PUT /annotations/{id}` returns 409 for split transactions.
- Quality gate passes.

---

## Task 3: `PUT /transactions/{id}/allocations` endpoint

### What

New endpoint to atomically replace a transaction's allocations with validation
and auto-correction.

### Pydantic model

```python
class AllocationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float
    category: str | None = None
    tags: list[str] | None = None
    note: str | None = None
```

### Endpoint spec

**`PUT /transactions/{id}/allocations`**
Auth: bearer token required.
Path param: `transaction_id: str`
Request body: `list[AllocationItem]` (JSON array)

**Validation steps (in order):**

1. If the list is empty → HTTP 422:
   `{"error": "at least one allocation is required"}`

2. Fetch the transaction; if not found → HTTP 404.

3. Compute balance:
   ```python
   tx_amount = round(transaction["amount"], 2)
   total = round(sum(item.amount for item in body), 2)
   diff = round(tx_amount - total, 2)
   ```

4. If `abs(diff) > 1.00` → HTTP 422:
   ```json
   {
     "error": "allocation amounts do not balance",
     "transaction_amount": <tx_amount>,
     "allocation_total": <total>,
     "difference": <diff>
   }
   ```

5. If `diff != 0.0` (but `abs(diff) <= 1.00`) → silently adjust last item:
   ```python
   body[-1].amount = round(body[-1].amount + diff, 2)
   ```

6. Build `AllocationRow` objects (with `created_at` / `updated_at` = now UTC ISO
   string; `tags` serialised as JSON if not None) and call `replace_allocations()`.

7. Return `_fetch_transaction_with_allocations(connection, transaction_id)`.

**Response shape** — same as `GET /transactions/{id}`:
```json
{
  "id": "txn_abc123",
  "amount": 100.00,
  "name": "AMAZON.COM",
  ...,
  "allocations": [
    {"id": 7, "amount": 60.00, "category": "groceries", "tags": ["household"],
     "note": "food", "updated_at": "..."},
    {"id": 8, "amount": 40.00, "category": "household", "tags": null,
     "note": null, "updated_at": "..."}
  ]
}
```

### Router wiring

Add the route to `routers/transactions.py`. The endpoint path is
`/transactions/{transaction_id}/allocations`.

No `_strict_params` needed (PUT endpoint, no query parameters accepted).
`AllocationItem` already has `extra="forbid"` via Pydantic.

Update `_TRANSACTIONS_ALLOWED_PARAMS` only if the constant covers this path —
it should not, since this is a PUT, not a GET.

### Tests

- 200: valid two-allocation split with exact amounts → both allocations returned.
- 200: single allocation (degenerate case) — works, returns one-element array.
- 200: amounts short by $0.50 → last allocation auto-corrected; no 422.
- 200: amounts over by $0.01 → last allocation auto-corrected; no 422.
- 422: amounts differ by $1.50 → error body includes `transaction_amount`,
  `allocation_total`, `difference`.
- 422: empty array.
- 422: extra field in body item (Pydantic `extra="forbid"`).
- 404: unknown `transaction_id`.
- 401: missing bearer token.
- After a successful PUT, `GET /transactions/{id}` returns the same
  `allocations` array (round-trip verification).
- After a split, `PUT /annotations/{id}` on the same transaction returns 409
  (integration of Task 2 restriction).

### Done when

- `PUT /transactions/{id}/allocations` is live.
- All validation and auto-correction paths tested.
- Quality gate passes.

---

## Task 4: CLI `ledger allocations`

### What

Two sub-operations under a new `allocations` command group: `show` (read-only
view of current allocation state) and `set` (replace-all via the new endpoint).

Both commands require the API base URL and API secret. Use the same config
loading and HTTP client pattern as any other CLI command that calls the server.

### `ledger allocations show <transaction_id>`

Calls `GET /transactions/{transaction_id}` and formats the result:

```
Transaction: txn_abc123
  Date:     2026-03-15
  Merchant: AMAZON.COM
  Amount:   $100.00

Allocations (2):
  #1   $60.00   groceries     [household]   food
  #2   $40.00   household     (no tags)     (no note)
  ──────────────────────────────────────────────
  Total: $100.00   ✓ Balanced
```

If allocation total ≠ transaction amount (edge case; should not occur in
normal operation), show:
```
  Total: $99.00   ⚠ Unbalanced (diff: $1.00)
```

Exits non-zero on HTTP error (404, 401, etc.) with a clear message.

### `ledger allocations set <transaction_id> --file <path>`

Reads a JSON file (array of allocation items matching `AllocationItem` schema)
and calls `PUT /transactions/{transaction_id}/allocations`. On success, renders
the result using the same format as `show`.

Pass `--file -` to read from stdin.

Example usage:
```bash
ledger allocations set txn_abc123 --file allocations.json
echo '[{"amount": 100.00, "category": "groceries"}]' \
  | ledger allocations set txn_abc123 --file -
```

Handle API error responses clearly:
- 404 → `"Transaction not found: <id>"`
- 409 → print the server message (shouldn't occur via this path, but be safe)
- 422 → print the validation error detail, including amounts if present
- 401 → `"Authentication failed — check CLAW_API_SECRET"`

### Tests

- `show` — formats balanced state correctly (amounts, tags, note, totals).
- `show` — formats single-allocation transaction correctly.
- `show` — displays unbalanced warning when totals differ.
- `show` — exits non-zero and prints message on 404.
- `set` — reads file, calls PUT endpoint, renders result.
- `set` — reads from stdin when `--file -`.
- `set` — prints 422 validation detail (amounts don't balance) without traceback.
- `set` — exits non-zero on 404.

### Done when

- `ledger allocations show <id>` and `ledger allocations set <id> --file <path>`
  work end-to-end.
- Quality gate passes.

---

## Task 5: Skill bundle updates

### What

Update both skill bundles for the new response shape, new endpoint, and
`PUT /annotations/{id}` restriction. No behavior changes to the agents; this
is documentation only.

### Changes required in both skill bundles

**Response shape update (all files that reference `allocation.*`):**

- Replace every reference to `allocation.category`, `allocation.tags`,
  `allocation.note`, `allocation.amount`, `allocation.id` with the indexed
  form: `allocations[0].category`, `allocations[0].tags`, etc.
- Note that `"allocations"` is always an array, never null. For unmodified
  (single-allocation) transactions, `allocations` has exactly one element.

Document the updated `allocation` object shape:
```json
"allocations": [
  {
    "id": 5,
    "amount": 60.00,
    "category": "groceries",
    "tags": ["household"],
    "note": "weekly shopping",
    "updated_at": "2026-03-25T10:00:00+00:00"
  }
]
```

**`PUT /transactions/{id}/allocations` (new endpoint — add to approved API
calls in both SKILL.md files):**

- Request: JSON array of `{amount, category?, tags?, note?}` items.
- Validation: amounts must sum to transaction amount; auto-corrects within
  $1.00; returns 422 if off by more.
- Response: full transaction with `"allocations": [...]`.
- Use when splitting a transaction across categories.

**`PUT /annotations/{id}` restriction:**

- Note that this endpoint returns **HTTP 409** if the transaction has more
  than one allocation.
- For split transactions, agents must use `PUT /transactions/{id}/allocations`.
- For single-allocation transactions, `PUT /annotations/{id}` continues to
  work as before (request body unchanged).

**`GET /spend` and `GET /spend/trends` field rename:**

- The field is now `allocation_count` (was `transaction_count`).
- Update any playbook or example response that references `transaction_count`.

### Hestia-specific additions

- Ingestion loop step 2: transactions with `allocations.length > 1` have
  already been split by an operator; Hestia should read (not overwrite)
  existing allocations on those transactions. Hestia should not call
  `PUT /annotations/{id}` on a split transaction (it will 409); use
  `PUT /transactions/{id}/allocations` if re-categorisation is needed.
- Annotation write checklist: add a pre-flight step — check
  `allocations.length`; if > 1, route to allocation endpoint instead.

### Athena-specific additions

- Spend rollup note: for split transactions, `GET /spend?category=groceries`
  correctly sums only the grocery allocation amounts (not the full transaction
  amount), because spend queries join and filter on `allocations`.
- Add playbook entry: "Reviewing split transactions" — query
  `GET /transactions?limit=500` and identify rows where the same `id` appears
  multiple times (each appearance is one allocation).

### Done when

- No skill file references `allocation.` (singular dot-access) or
  `transaction_count`.
- Both SKILL.md files list `PUT /transactions/{id}/allocations` in approved
  API calls.
- `PUT /annotations/{id}` 409 behaviour is documented.
- Quality gate passes.

---

## Acceptance criteria for Sprint 23

- A transaction can be split into multiple allocations via
  `PUT /transactions/{id}/allocations`.
- Allocation amounts always reconcile to the transaction total (auto-corrected
  within $1.00; rejected if off by more).
- `GET /transactions/{id}` returns `"allocations": [...]` for all transactions,
  including split ones.
- `GET /transactions` list returns one row per allocation; each row has
  `"allocations": [<one item>]`.
- `PUT /annotations/{id}` returns 409 for split transactions.
- `ledger allocations show <id>` displays the current allocation state.
- `ledger allocations set <id> --file <path>` replaces allocations and
  confirms the result.
- `GET /spend` and `GET /spend/trends` responses use `allocation_count`.
- Both skill bundles reflect the new shape, new endpoint, and restriction.
- Unmodified (single-allocation) transactions behave identically to Sprint 22.
- Full quality gate passes with no regressions.

## Explicitly deferred

- Individual allocation create/patch/delete sub-operations (replace-all is the
  only write path in M21).
- `DELETE /transactions/{id}/allocations` to reset to a single seed allocation.
- `category` filter on `GET /transactions` (revisit after allocation model
  stabilises).
- Removal of `annotations` table (M22).
