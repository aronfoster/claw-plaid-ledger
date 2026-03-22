# Known Bugs

## Bug 1: `range` parameter ignored on `GET /transactions`

**Status:** Confirmed
**Reported by:** Athena

### Description

`GET /spend` accepts a `range` query parameter (e.g. `range=last_month`, `range=this_month`) and correctly resolves it to a bounded date window before querying the database. `GET /transactions` does **not** accept a `range` parameter at all — the `TransactionListQuery` model only exposes `start_date` and `end_date` as explicit ISO-date strings.

When a client passes `range=last_month` or `range=this_month` to `/transactions`, the parameter is silently ignored (FastAPI discards unknown query params). The endpoint then runs with no date filter and returns the full transaction dataset, paginated at the default limit of 100 (max 500), with a `total` reflecting all rows rather than the intended window.

### Observed behaviour

- `/spend?range=last_month` → correctly returns spend aggregated over Feb 1–28.
- `/transactions?range=last_month` → ignores the `range` value, returns 671 transactions spanning the entire history (Feb 22 – Mar 20 in the reported case).

### Root cause

`TransactionListQuery` (server.py ~line 519) has no `range` field and no date-resolution logic equivalent to `_resolve_spend_dates()` (server.py lines 606–648). The `/transactions` route therefore never converts a named range to `start_date`/`end_date` before building the `TransactionQuery`.

### Affected code

- `src/claw_plaid_ledger/server.py` — `TransactionListQuery` model and `list_transactions()` route handler (lines ~519–571)

---

## Bug 2: Annotations missing from `GET /transactions` list results

**Status:** Confirmed
**Reported by:** Athena

### Description

`GET /transactions/{id}` returns a full transaction object that includes a nested `annotation` field (with `category`, `note`, `tags`, `updated_at`). `GET /transactions` (the list endpoint) returns the same base fields but **never includes annotation data**, even for transactions that have annotations stored in the database.

This makes it impossible to filter or display annotated transactions from the list view without issuing one additional request per transaction.

### Observed behaviour

- `GET /transactions/{id}` → returns `{ ..., "annotation": { "category": "...", "note": "...", "tags": [...], "updated_at": "..." } }`.
- `GET /transactions` → returns `{ "transactions": [{ "id": "...", ... }], "total": ..., ... }` with no `annotation` key on any transaction object, even when annotations exist.

### Root cause

`list_transactions()` calls `query_transactions()` from `db.py`, which returns raw transaction rows built without a JOIN to the `annotations` table. The helper `_fetch_transaction_with_annotation()` (server.py lines 754–779) that merges annotation data is only called from the single-record route `get_transaction_detail()`.

### Affected code

- `src/claw_plaid_ledger/server.py` — `list_transactions()` route handler (lines ~536–571) and `_fetch_transaction_with_annotation()` helper (lines ~754–779)
- `src/claw_plaid_ledger/db.py` — `query_transactions()` (lines ~365–463); no JOIN to `annotations` table
