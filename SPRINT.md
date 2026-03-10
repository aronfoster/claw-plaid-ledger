# Sprint 5 — M4: Agent API and annotation layer

## Sprint goal

Expose a typed REST API so OpenClaw agents can query the transaction ledger
and write durable annotations — without ever touching SQLite directly.
This sprint adds three new endpoints (`GET /transactions`,
`GET /transactions/{id}`, `PUT /annotations/{transaction_id}`), the supporting
`annotations` table, and updates `ARCHITECTURE.md` to serve as the source of
truth for the OpenClaw SKILL definition.

## Working agreements

- Keep changes small and independently reviewable.
- Prefer one standalone task per PR unless a dependency forces a pair.
- Preserve strict quality gates on every PR:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest`
- Use standard-library `sqlite3` for the database layer.
- Add appropriate unit and integration tests for each task.

## Conventions and data notes

**Transaction identifier in URLs:** use `plaid_transaction_id` (the
Plaid-issued string, e.g. `"Ax9bz3KQ..."`). This is the stable external key
already stored as `UNIQUE` in the `transactions` table; it is safe for agents
to cache and reference across sessions.

**Effective date:** many query operations need a single date per transaction.
Use `COALESCE(posted_date, authorized_date)` as the effective date. For posted
transactions `posted_date` is set; for pending ones only `authorized_date` is
set.

**Amount sign convention:** Plaid uses positive = money leaving the account
(debit/expense), negative = money entering (credit/income). The API exposes
amounts exactly as stored — do not invert.

**Tags storage:** tags are stored as a JSON text string in SQLite (e.g.
`'["food", "recurring"]'`). The API accepts and returns them as a JSON array.
The server is responsible for `json.dumps` on write and `json.loads` on read.

**Annotation ownership:** the sync engine must never read from or write to
the `annotations` table. The annotation layer is entirely agent-owned;
Plaid-sourced tables (`transactions`, `accounts`, `sync_state`) remain
immutable from the agent's perspective.

**OpenAPI spec:** FastAPI generates `/openapi.json` and `/docs` automatically.
These are served without authentication (consistent with the local-only
security posture). The OpenAPI spec becomes the source of truth for the
OpenClaw SKILL definition.

---

## Task breakdown

### Task 1: `annotations` table and DB helpers ✅ DONE

**Scope**

Add the `annotations` table to `schema.sql` and the corresponding helpers to
`db.py`. Nothing else changes in this task.

**Schema** — add to `schema.sql` using `CREATE TABLE IF NOT EXISTS` so
`init-db` is safe to run against an existing database:

```sql
CREATE TABLE IF NOT EXISTS annotations (
    id                   INTEGER PRIMARY KEY,
    plaid_transaction_id TEXT NOT NULL UNIQUE
                         REFERENCES transactions(plaid_transaction_id),
    category             TEXT,
    note                 TEXT,
    tags                 TEXT,          -- JSON array stored as text
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
```

**`db.py` additions:**

- `AnnotationRow` frozen dataclass with fields:
  `plaid_transaction_id: str`, `category: str | None`, `note: str | None`,
  `tags: str | None`, `created_at: str`, `updated_at: str`

- `upsert_annotation(conn: sqlite3.Connection, row: AnnotationRow) -> None`
  - `INSERT OR REPLACE` pattern; preserve original `created_at` on update using
    the same approach as `upsert_transaction` — explicitly pass `created_at`
    from the existing row when updating, or the current UTC timestamp when
    inserting
  - Straightforward implementation: on conflict (UNIQUE on
    `plaid_transaction_id`), overwrite all fields except `created_at`
  - Recommended: `INSERT INTO annotations (...) VALUES (...) ON CONFLICT
    (plaid_transaction_id) DO UPDATE SET category=excluded.category,
    note=excluded.note, tags=excluded.tags, updated_at=excluded.updated_at`
    (this naturally preserves `created_at`)

- `get_annotation(conn: sqlite3.Connection, plaid_transaction_id: str) ->
  AnnotationRow | None`
  - `SELECT` by `plaid_transaction_id`; return `None` if not found

**Done when**

- Running `ledger init-db` against a database that already has `transactions`,
  `accounts`, and `sync_state` tables creates `annotations` without error and
  without touching the other tables
- `upsert_annotation` followed by a second `upsert_annotation` preserves
  `created_at` and updates `updated_at`
- `get_annotation` returns `None` for an unknown `plaid_transaction_id`

**Testing expectations**

- Test: `upsert_annotation` inserts a new row; all fields round-trip correctly
- Test: second `upsert_annotation` for same `plaid_transaction_id` updates
  `category`, `note`, `tags`, `updated_at` while preserving `created_at`
- Test: `get_annotation` returns `None` for an ID not in `annotations`
- Test: schema idempotency — calling `ensure_schema()` (or `init-db`) twice
  does not raise

---

### Task 2: `GET /transactions` list endpoint ✅ DONE

**Scope**

Add a query helper to `db.py` and the corresponding endpoint to `server.py`.

**`db.py` addition — `query_transactions`:**

```python
def query_transactions(
    conn: sqlite3.Connection,
    *,
    start_date: str | None = None,     # YYYY-MM-DD inclusive
    end_date: str | None = None,       # YYYY-MM-DD inclusive
    account_id: str | None = None,     # plaid_account_id exact match
    pending: bool | None = None,
    min_amount: float | None = None,   # inclusive
    max_amount: float | None = None,   # inclusive
    keyword: str | None = None,        # LIKE match on name + merchant_name
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, object]], int]:
    ...
```

Implementation notes:
- Build the `WHERE` clause dynamically from whichever filters are provided
- Date filter: `COALESCE(posted_date, authorized_date) BETWEEN ? AND ?`
- `pending` maps to SQLite `0`/`1`
- `keyword` filter: `(name LIKE ? OR merchant_name LIKE ?)` where the
  parameter is `f"%{keyword}%"`; SQLite `LIKE` is case-insensitive for ASCII
  by default — no `COLLATE NOCASE` needed
- Return a tuple `(rows, total)` where `total` is the full matching count
  (run a `SELECT COUNT(*)` with the same `WHERE` clause before applying
  `LIMIT`/`OFFSET`)
- Each dict in `rows` must include:
  `id` (= `plaid_transaction_id`), `account_id` (= `plaid_account_id`),
  `amount`, `iso_currency_code`, `name`, `merchant_name`, `pending` (bool,
  not int), `date` (= `COALESCE(posted_date, authorized_date)`)

**Endpoint:**

```
GET /transactions
Authorization: Bearer <token>
```

Query parameters (all optional):

| Param | Type | Description |
|---|---|---|
| `start_date` | `YYYY-MM-DD` | Filter by effective date ≥ |
| `end_date` | `YYYY-MM-DD` | Filter by effective date ≤ |
| `account_id` | string | Exact match on `plaid_account_id` |
| `pending` | bool | `true` or `false` |
| `min_amount` | float | Amount ≥ (Plaid sign: positive = debit) |
| `max_amount` | float | Amount ≤ |
| `keyword` | string | Case-insensitive substring match on `name` and `merchant_name` |
| `limit` | int | Default `100`, max `500` |
| `offset` | int | Default `0` |

Response (HTTP 200):

```json
{
  "transactions": [
    {
      "id": "<plaid_transaction_id>",
      "account_id": "<plaid_account_id>",
      "amount": 12.34,
      "iso_currency_code": "USD",
      "name": "Starbucks",
      "merchant_name": "Starbucks",
      "pending": false,
      "date": "2024-01-15"
    }
  ],
  "total": 150,
  "limit": 100,
  "offset": 0
}
```

- `limit > 500` returns HTTP 422 with a descriptive validation message;
  use FastAPI's `Query(le=500)` to get this automatically
- Requires bearer token; missing/invalid token returns 401
- Empty result set returns HTTP 200 with `"transactions": []` and `"total": 0`

**Done when**

- `GET /transactions` returns 200 with the pagination envelope
- All filters work individually and in combination
- `limit=501` returns 422
- Empty database returns `{"transactions": [], "total": 0, "limit": 100,
  "offset": 0}`

**Testing expectations**

- Test: no filters returns all transactions (up to default limit), correct
  `total`
- Test: `start_date` and `end_date` filter correctly on effective date
- Test: `account_id` filters to the matching account only
- Test: `pending=true` returns only pending; `pending=false` returns only
  posted
- Test: `min_amount=10&max_amount=50` returns only transactions in that range
- Test: `keyword` matches on `name`; `keyword` matches on `merchant_name`;
  `keyword` is case-insensitive
- Test: `offset` and `limit` page correctly; `total` reflects unfiltered count
- Test: `limit=501` returns 422
- Test: missing bearer token returns 401; wrong token returns 401
- Test: empty DB returns 200 with empty list and `total=0`

---

### Task 3: `GET /transactions/{transaction_id}` detail endpoint ✅ DONE

**Scope**

Add a single-row query helper to `db.py` and the detail endpoint to `server.py`.

**`db.py` addition — `get_transaction`:**

```python
def get_transaction(
    conn: sqlite3.Connection,
    plaid_transaction_id: str,
) -> dict[str, object] | None:
    ...
```

Returns a dict with all transaction columns, or `None` if not found. The
returned dict includes the same fields as the list response plus `raw_json`.
The endpoint merges in the annotation separately (call `get_annotation` after
`get_transaction`).

**Endpoint:**

```
GET /transactions/{transaction_id}
Authorization: Bearer <token>
```

- `transaction_id` in the path is the `plaid_transaction_id` string
- Returns HTTP 404 if not found
- Returns HTTP 200 with the full transaction detail plus annotation:

```json
{
  "id": "<plaid_transaction_id>",
  "account_id": "<plaid_account_id>",
  "amount": 12.34,
  "iso_currency_code": "USD",
  "name": "Starbucks",
  "merchant_name": "Starbucks",
  "pending": false,
  "date": "2024-01-15",
  "raw_json": "{...}",
  "annotation": {
    "category": "food",
    "note": "Morning coffee",
    "tags": ["discretionary", "recurring"],
    "updated_at": "2024-01-16T10:30:00Z"
  }
}
```

- `annotation` is `null` if no annotation exists for this transaction
- `tags` in the response is a parsed JSON list (not the raw string stored in
  SQLite); if stored value is `null`, return `null` for tags
- `raw_json` is the raw Plaid API payload stored at sync time; may be `null`
  for transactions synced before this field was populated

**Done when**

- Known `transaction_id` returns 200 with full detail
- Known `transaction_id` with no annotation returns 200 with `"annotation": null`
- Unknown `transaction_id` returns 404
- `tags` is a JSON list in the response (not a string)

**Testing expectations**

- Test: known ID with no annotation returns 200, `annotation` is `null`
- Test: known ID with annotation returns 200, annotation fields are correct,
  `tags` is a Python list (not a string)
- Test: `annotation.tags = null` when stored tags is `null`
- Test: unknown ID returns 404
- Test: missing bearer token returns 401; wrong token returns 401

---

### Task 4: `PUT /annotations/{transaction_id}` upsert endpoint

**Scope**

Add the annotation write endpoint to `server.py`. The DB helper from Task 1
(`upsert_annotation`) is used directly.

**Endpoint:**

```
PUT /annotations/{transaction_id}
Authorization: Bearer <token>
Content-Type: application/json
```

- `transaction_id` in the path is the `plaid_transaction_id` string
- Request body (all fields optional; omitted fields are stored as `null`):

```json
{
  "category": "food",
  "note": "Morning coffee",
  "tags": ["discretionary", "recurring"]
}
```

- This is a **full replace**, not a partial PATCH: every PUT completely
  overwrites the annotation row. If an agent omits `note`, the stored `note`
  becomes `null`.
- `tags` must be a JSON array of strings or `null`; the server stores it as
  `json.dumps(tags)` (or `null` if absent/null in the body)
- Returns HTTP 404 if `transaction_id` does not exist in the `transactions`
  table — agents cannot annotate phantom transactions. Check existence with
  `get_transaction` before calling `upsert_annotation`.
- Returns HTTP 200 `{"status": "ok"}` on successful create or update
- Requires bearer token; missing/invalid token returns 401

**Pydantic request model** (define in `server.py` or a new `api_models.py`):

```python
class AnnotationRequest(BaseModel):
    category: str | None = None
    note: str | None = None
    tags: list[str] | None = None
```

**Done when**

- `PUT /annotations/{id}` on a valid transaction creates the annotation and
  returns 200
- A second PUT replaces the annotation; `created_at` is preserved
- `PUT /annotations/{unknown_id}` returns 404
- A PUT followed by `GET /transactions/{id}` returns the annotation correctly
  (tags as a list)

**Testing expectations**

- Test: PUT creates a new annotation; response is `{"status": "ok"}`
- Test: second PUT replaces annotation; `created_at` unchanged (verify via
  `get_annotation` in DB); `updated_at` changes
- Test: PUT with `{}` (empty body) stores all-null annotation; returns 200
- Test: PUT with `tags: []` stores empty list correctly; round-trips as `[]`
- Test: PUT for unknown `transaction_id` returns 404
- Test: missing bearer token returns 401; wrong token returns 401
- Test: end-to-end — PUT annotation, then GET transaction detail, verify
  `annotation` block matches

---

### Task 5: `ARCHITECTURE.md` update

**Scope**

Update `ARCHITECTURE.md` to reflect all additions from this sprint. No code
changes; documentation only.

Sections to add or update:

1. **Schema** — add `annotations` table with column descriptions, noting that
   `tags` is stored as JSON text, and that this table is entirely agent-owned
   (sync engine never touches it)

2. **API endpoints** — extend the endpoints table to include:

   | Method | Path | Auth | Description |
   |---|---|---|---|
   | `GET` | `/transactions` | Bearer | Paginated, filtered transaction list |
   | `GET` | `/transactions/{transaction_id}` | Bearer | Single transaction with annotation |
   | `PUT` | `/annotations/{transaction_id}` | Bearer | Upsert annotation for a transaction |
   | `GET` | `/openapi.json` | None | Auto-generated OpenAPI spec (FastAPI) |
   | `GET` | `/docs` | None | Swagger UI (FastAPI, local use only) |

3. **Query parameters for `GET /transactions`** — document all eight filter
   params with types, defaults, and the effective-date definition
   (`COALESCE(posted_date, authorized_date)`)

4. **Annotation response shape** — document the `annotation` object embedded
   in `GET /transactions/{id}`, including `tags` being a JSON array

5. **OpenAPI / SKILL definition** — note that `GET /openapi.json` is the
   canonical machine-readable spec and is intended to seed the OpenClaw SKILL
   definition for M7

6. **Data flow** — update to reflect the new read path:
   `SQLite → Agent API → OpenClaw agent`

**Done when**

- A developer can implement the full M4 API surface from `ARCHITECTURE.md`
  alone without reading the code
- The OpenAPI endpoint and Swagger UI are documented

---

## Acceptance criteria for the sprint

- `GET /transactions` returns a correctly paginated, filterable list; all
  eight filter parameters work independently and in combination
- `GET /transactions/{id}` returns full transaction detail with annotation
  merged in (`null` when absent); `tags` is a JSON array in the response
- `PUT /annotations/{id}` creates or fully replaces an annotation; 404 for
  unknown transactions; `created_at` is preserved on updates
- The `annotations` table is isolated from the sync engine — no sync code
  reads from or writes to it
- `init-db` on an existing database creates `annotations` without error
- All three new endpoints require a valid bearer token; 401 on
  missing/invalid
- FastAPI's `/openapi.json` and `/docs` are reachable without auth and
  accurately describe all endpoints
- Quality gate passes on all PRs (`ruff format`, `ruff check`, `mypy`,
  `pytest`)
- `ARCHITECTURE.md` reflects the new schema and full API surface

## Explicitly deferred

- Per-agent token scoping (different tokens for read-only vs. read-write)
- Keyword search on annotation `note` field
- `DELETE /annotations/{transaction_id}`
- Bulk annotation operations
- OpenClaw notification on new transactions (M5)
- Merchant normalization and category hints (M6)
- OpenClaw SKILL definition file (M7)
