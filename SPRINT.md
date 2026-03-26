# Sprint 23 — M21: Manual Allocation Editing

## Sprint goal

Make multi-allocation transactions fully usable. An operator can split one
imported transaction across multiple categories, with the system enforcing
that allocation amounts always reconcile to the transaction total. Unmodified
(single-allocation) transactions continue to work exactly as before.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **Response shape — breaking change** — two distinct shapes, intentionally
  different:
  - `GET /transactions` list: `"allocation": {...}` (singular object). Each list
    row *is* one (transaction, allocation) pair, so the singular key is accurate.
    The allocation fields (id, amount, category, tags, note, updated_at) are
    unchanged; only the key name changes from the M20 shape.
  - `GET /transactions/{id}` detail, `PUT /annotations/{id}` response, and the
    new `PUT /transactions/{id}/allocations` response: `"allocations": [...]`
    (array, never null). For unsplit transactions this array has one element;
    for split transactions it has all allocations ordered by `id ASC`.
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

## Task 1: DB layer — `replace_allocations` and all-allocation fetching ✅ DONE

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

The list endpoint keeps `"allocation": {...}` (singular). Each list row is one
(transaction, allocation) pair, so the singular key accurately describes what
the row carries. The field name is **unchanged from M20** — `_allocation_from_joined_row`
and the projection stay as-is. No edits needed here.

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
- `GET /transactions` list — each row still has `"allocation"` key (singular,
  unchanged from M20); verify no regression.
- `PUT /annotations/{id}` — response has `"allocations"` key (array).
- `PUT /annotations/{id}` — returns HTTP 409 when the transaction has 2
  allocations; response body contains `"allocation_count": 2`.
- `GET /spend` — response has `"allocation_count"`, not `"transaction_count"`.
- `GET /spend/trends` — each bucket has `"allocation_count"`.
- Update all existing tests that check `response["allocation"]` on the detail
  endpoint (not the list) or `result["transaction_count"]` to use the new keys.

### Done when

- `GET /transactions/{id}` and `PUT /annotations/{id}` responses contain
  `"allocations"` (array); `GET /transactions` list retains `"allocation"`
  (singular, unchanged).
- `"transaction_count"` is gone from spend responses.
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

Update both skill bundles: remove stale annotation-centric guidance, update
response field references for the detail-view shape change, add the new
endpoint, and document the `PUT /annotations/{id}` restriction. This is
documentation-only — no code changes.

### Step 1 — Remove annotation-centric skill definitions

The skill bundles currently frame `PUT /annotations/{id}` as the primary write
surface for categorisation. That framing is now obsolete. Remove or replace:

- Any checklist or playbook step that positions `PUT /annotations/{id}` as the
  default or recommended path for writing category/tags/note.
- Any section header, workflow description, or example that uses the word
  "annotation" to describe what is now an allocation operation.
- Any example response body that shows an `"annotation"` key.

Replace removed content with allocation-first equivalents (see Steps 2–3).

`PUT /annotations/{id}` must still appear in the approved API calls list as a
**narrow compatibility shim** with a clear caveat: single-allocation
transactions only; returns 409 if the transaction has been split.

### Step 2 — Response shape update (detail view only)

The list endpoint (`GET /transactions`) retains `"allocation": {...}` (singular,
unchanged from M20). Only the detail-view endpoints change:

- `GET /transactions/{id}` and `PUT /transactions/{id}/allocations` return
  `"allocations": [...]` (array).

Update all skill file references to allocation fields that appear in a **detail
view context**:

- `allocation.category` → `allocations[0].category`
- `allocation.tags` → `allocations[0].tags`
- `allocation.note` → `allocations[0].note`
- `allocation.amount` → `allocations[0].amount`
- `allocation.id` → `allocations[0].id`

For the list view, `allocation.category` etc. remain correct (singular object).
Make the context explicit in the skill docs so agents know which shape to
expect in each context.

Document the detail-view `"allocations"` shape:
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
`"allocations"` is always present and never null. For unsplit transactions it
has exactly one element.

### Step 3 — Document new and changed endpoints

**Add `PUT /transactions/{id}/allocations` to approved API calls:**

- Request: JSON array of `{amount, category?, tags?, note?}` items.
- Validation: amounts must sum to transaction amount; auto-corrects within
  $1.00; returns 422 if off by more (error body includes `transaction_amount`,
  `allocation_total`, `difference`).
- Response: full transaction detail with `"allocations": [...]`.
- This is the primary write surface for categorisation going forward.

**Update `PUT /annotations/{id}` entry:**

- Returns HTTP 409 if the transaction has more than one allocation. Do not
  call this endpoint on split transactions.
- For all practical purposes, agents should use
  `PUT /transactions/{id}/allocations` for all allocation writes (it works
  for both split and unsplit transactions). `PUT /annotations/{id}` is a
  compatibility shim.

**`GET /spend` and `GET /spend/trends` field rename:**

- `transaction_count` is now `allocation_count`. Update any example response
  or playbook that references the old name.

### Step 4 — Hestia-specific guidance

- Replace the annotation write checklist with an allocation write checklist.
  The new pre-flight step: check `allocations.length` from the detail view;
  use `PUT /transactions/{id}/allocations` for all writes (it handles both
  cases correctly).
- Ingestion loop: a transaction with `allocations.length > 1` has been split
  by an operator. Hestia should not overwrite an operator-defined split;
  flag for Athena review instead.

### Step 5 — Athena-specific guidance

- Spend rollup: for split transactions, `GET /spend?category=groceries`
  correctly sums only grocery allocation amounts, not the full transaction
  amount.
- Add playbook entry "Reviewing split transactions": in `GET /transactions`
  list results, the same transaction `id` appears once per allocation; grouping
  by `id` reveals split transactions and their per-category breakdown.

### Done when

- No skill file positions `PUT /annotations/{id}` as the primary write path.
- No skill file contains stale "annotation" framing for category/tags/note
  operations.
- List-view vs. detail-view shape distinction is clearly documented.
- Both SKILL.md files list `PUT /transactions/{id}/allocations` in approved
  API calls.
- `allocation_count` replaces `transaction_count` in all examples.
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
  `"allocation": {...}` (singular, unchanged from M20).
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
