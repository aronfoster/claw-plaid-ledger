# Sprint 18 — M16: Spend Trends

## Sprint goal

Replace multiple `GET /spend` calls and manual stitching with a single
`GET /spend/trends` endpoint that returns spend aggregated by calendar month,
oldest to newest, for any lookback window.

## Why this sprint exists

Post-M15 trend analysis requires one `GET /spend` call per month plus
client-side aggregation. For a six-month view that is six round-trips and
bespoke date arithmetic — tedious for agents and operators alike. (BUG-011)

## Working agreements

- Single task; ships as one PR.
- All Python changes must pass the quality gate before merge:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Doc-only changes (skill files) still run the gate to confirm no regressions.
- Mark completed task `✅ DONE` in this file before committing.

---

## Task 1: BUG-011 — GET /spend/trends + skill doc updates ✅ DONE

### Background

There is no endpoint that returns spend aggregated by calendar month.
Producing a month-over-month view currently requires one `GET /spend` call
per month, then manually stitching results. This task adds `GET /spend/trends`
and documents it in Athena's skill bundle.

### Design decisions

**Response shape — plain array.** The response is a JSON array of bucket
objects, oldest first. No wrapper object. Agents know which filters they
applied; echoing them back is unnecessary.

```json
[
  {"month": "2025-10", "total_spend": 3241.50, "transaction_count": 47, "partial": false},
  {"month": "2025-11", "total_spend": 2150.00, "transaction_count": 38, "partial": false},
  {"month": "2025-12", "total_spend": 4010.75, "transaction_count": 55, "partial": false},
  {"month": "2026-01", "total_spend": 2980.00, "transaction_count": 41, "partial": false},
  {"month": "2026-02", "total_spend": 3100.25, "transaction_count": 44, "partial": false},
  {"month": "2026-03", "total_spend":  850.00, "transaction_count": 12, "partial": true}
]
```

**Zero-fill.** Months with no qualifying transactions always appear in the
response with `total_spend: 0.0` and `transaction_count: 0`. The caller
always receives exactly `months` buckets.

**`partial` flag.** The current calendar month (i.e. the month containing
`_today()`) is marked `partial: true`. All prior months are `partial: false`.

**`months` parameter.** Integer lookback window, default `6`, minimum `1`.
No upper bound — SQLite GROUP BY queries are cheap. Enforce minimum with
`Field(ge=1)` on the Pydantic model.

**`range` not supported.** `months` already defines the time window for
trends. The `range` shorthand from `GET /spend` does not apply here.

**Filter parity with `GET /spend`.** Supports `owner`, `tags` (multi-value),
`category`, `tag` (singular), `account_id`, `view`, and `include_pending` —
all with identical semantics. A trend query and a point-in-time spend query
over the same filters are directly comparable.

**`_today()` is the date anchor.** The DB function receives `today: date`
explicitly (not via `date.today()` internally) so tests can control it with
the existing `_patch_today` helper. The server layer passes `_today()`.

**`noqa: S608` applies.** `query_spend_trends()` builds SQL from hard-coded
fragments with all user-supplied values bound via `?` placeholders, identical
to `query_spend()`. The same S608 rationale applies; extend the inline comment
to cover the new function rather than adding a second noqa block.

### Scope

#### `db.py` — new dataclass and function

**New `SpendTrendsQuery` dataclass** (after `SpendQuery`):

```python
@dataclass(frozen=True)
class SpendTrendsQuery:
    """Filters for the monthly spend trends query."""

    months: int
    owner: str | None = None
    tags: tuple[str, ...] = ()
    include_pending: bool = False
    canonical_only: bool = True
    account_id: str | None = None
    category: str | None = None
    tag: str | None = None
```

**New `query_spend_trends()` function:**

```python
def query_spend_trends(
    connection: sqlite3.Connection,
    query: SpendTrendsQuery,
    today: date,
) -> list[dict[str, object]]:
    """Return monthly spend buckets, oldest → newest, zero-filled."""
```

Implementation steps:

1. **Generate month labels.** Produce a list of `YYYY-MM` strings of length
   `query.months`, ending with the current month and going backwards. Example
   for `today=2026-03-15`, `months=3`: `["2026-01", "2026-02", "2026-03"]`.

   ```python
   labels: list[str] = []
   year, month = today.year, today.month
   for _ in range(query.months):
       labels.append(f"{year}-{month:02d}")
       month -= 1
       if month == 0:
           month = 12
           year -= 1
   labels.reverse()
   ```

2. **Derive date window.**
   - `start_date = f"{labels[0]}-01"` — first day of the oldest month.
   - `end_date = today.isoformat()` — today (inclusive).

3. **Build WHERE clause.** Mirror `query_spend()` exactly:
   - Date range on `COALESCE(t.posted_date, t.authorized_date)`.
   - `a.canonical_account_id IS NULL` when `canonical_only=True`.
   - `t.pending = 0` unless `include_pending=True`.
   - `a.owner = ?` when owner is set (requires accounts join).
   - Per-tag `EXISTS (SELECT 1 FROM json_each(ann.tags) WHERE value = ?)` for
     each entry in `query.tags`.
   - `t.plaid_account_id = ?` when `account_id` is set.
   - `LOWER(ann.category) = LOWER(?)` when `category` is set.
   - `EXISTS (SELECT 1 FROM json_each(ann.tags) WHERE LOWER(value) = LOWER(?))`
     when singular `tag` is set.

   The `need_accounts_join` and join strings follow the same conditional logic
   as `query_spend()`.

4. **Run GROUP BY query.**

   ```python
   month_expr = f"strftime('%Y-%m', {effective_date_sql})"
   rows = connection.execute(
       (
           f"SELECT {month_expr} AS month, "  # noqa: S608
           "COALESCE(SUM(t.amount), 0.0), COUNT(*) "
           "FROM transactions t "
           f"{accounts_join}"
           f"{annotations_join}"
           f"WHERE {where_sql} "
           "GROUP BY month "
           "ORDER BY month ASC"
       ),
       params,
   ).fetchall()
   ```

   S608 rationale: identical to `query_spend()` — all fragments are
   hard-coded SQL; all user-supplied values are `?`-bound. Extend the existing
   S608 comment in `query_spend()` to reference `query_spend_trends()` as
   well, or add an equivalent inline comment here.

5. **Merge and zero-fill.** Build a lookup dict from the SQL results, then
   map over `labels`:

   ```python
   current_month = f"{today.year}-{today.month:02d}"
   results: dict[str, tuple[float, int]] = {
       str(row[0]): (float(row[1]), int(row[2])) for row in rows
   }
   return [
       {
           "month": label,
           "total_spend": results.get(label, (0.0, 0))[0],
           "transaction_count": results.get(label, (0.0, 0))[1],
           "partial": label == current_month,
       }
       for label in labels
   ]
   ```

#### `server.py` — new Pydantic model and endpoint

**New `SpendTrendsListQuery` model** (after `SpendListQuery`):

```python
class SpendTrendsListQuery(BaseModel):
    """Scalar query parameters for GET /spend/trends."""

    months: int = Field(default=6, ge=1)
    owner: str | None = None
    include_pending: bool | None = None
    view: Literal["canonical", "raw"] = "canonical"
    account_id: str | None = None
    category: str | None = None
    tag: str | None = None
```

`Field` must be imported from `pydantic`. `bool | None` avoids FBT001/FBT002
(same pattern as `SpendListQuery.include_pending`).

**New endpoint:**

```python
@app.get("/spend/trends", dependencies=[Depends(require_bearer_token)])
def get_spend_trends(
    params: Annotated[SpendTrendsListQuery, Depends()],
    tags: Annotated[list[str] | None, Query()] = None,
) -> list[dict[str, object]]:
    """
    Return spend aggregated by calendar month for a lookback window.

    Returns exactly ``months`` buckets ordered oldest → newest. The
    current (in-progress) calendar month is flagged ``partial: true``
    so callers know not to compare it directly against complete months.
    Months with no qualifying transactions appear as zero-filled buckets.

    Supports the same filters as ``GET /spend`` (``owner``, ``tags``,
    ``category``, ``tag``, ``account_id``, ``view``,
    ``include_pending``) for direct comparability.
    """
    resolved_tags: list[str] = tags or []
    include_pending = params.include_pending is True
    config = load_config()
    trends_query = SpendTrendsQuery(
        months=params.months,
        owner=params.owner,
        tags=tuple(resolved_tags),
        include_pending=include_pending,
        canonical_only=params.view == "canonical",
        account_id=params.account_id,
        category=params.category,
        tag=params.tag,
    )
    with sqlite3.connect(config.db_path) as connection:
        return query_spend_trends(connection, trends_query, _today())
```

#### `tests/test_server.py` — new test class

Add a `TestGetSpendTrendsEndpoint` class. All tests in this class must call
`_patch_today(monkeypatch, "2026-03-15")` (or another fixed date) so bucket
counts and month labels are deterministic.

**Seed data helper.** Add a `_seed_trends_data(db_path)` function (or reuse
existing helpers if they already provide multi-month coverage). The seed must
include:

- Transactions in at least three distinct calendar months prior to the fixed
  "today" month (e.g. 2025-12, 2026-01, 2026-02, 2026-03 with `today` fixed
  to 2026-03-15).
- Transactions belonging to two different owners (`alice`, `bob`).
- Transactions on two different account IDs.
- At least one transaction with an annotation (`category`, `tags`) to support
  filter parity tests.
- At least one pending transaction to support `include_pending` tests.
- At least one month in the lookback window with no transactions so the
  zero-fill path is exercised.

**Minimum required tests:**

*Shape and ordering:*
- Default `?months=6` returns exactly 6 buckets.
- Buckets are ordered oldest → newest (ascending `month` strings).
- Only the last bucket (`2026-03`) has `partial: true`; all others have
  `partial: false`.
- `?months=1` returns a single bucket with `partial: true`.
- `?months=3` returns exactly 3 buckets.

*Zero-fill:*
- A month in the window with no transactions appears with
  `total_spend: 0.0` and `transaction_count: 0`.

*Totals sanity:*
- The sum of `total_spend` across all buckets equals what a single
  `GET /spend` call over the same window (with matching filters) returns.

*Filter parity:*
- `?owner=alice` — only Alice's transactions contribute to each bucket.
- `?account_id=<id>` — only that account's transactions are counted.
- `?category=<value>` — only annotated transactions with that category count
  (case-insensitive: `Software` and `software` both match).
- `?tag=<value>` — only transactions with that singular tag count
  (case-insensitive).
- `?tags=a&tags=b` — AND semantics; only transactions tagged with both count.
- `?view=raw` — suppressed accounts are included (raw view, no canonical
  filtering).
- `?include_pending=true` — pending transactions are counted; without the
  flag they are excluded.

*Validation:*
- `?months=0` returns HTTP 422 (below minimum).
- `?months=-1` returns HTTP 422.

*Auth:*
- Request without `Authorization` header returns HTTP 401.

### Skill doc updates

Only Athena's skill bundle is updated. Hestia is not permitted to call
`GET /spend/trends` and her docs remain unchanged.

#### `skills/athena-ledger/SKILL.md`

1. Add to the **Approved API calls** list:

   ```
   9. `GET /spend/trends` — monthly spend buckets for a lookback window;
      supports the same filters as `GET /spend`
   ```

2. Under **Core analysis workflows**, add a new section after the existing
   workflow entries:

   ```markdown
   ### 4) Month-over-month trends

   Use `GET /spend/trends` when the question involves change over time
   (e.g. "is spending increasing?", "which month was the most expensive?").

   - `?months=<n>` controls the lookback window (default 6, no upper bound).
   - The current month is flagged `partial: true` — treat it as incomplete
     and avoid direct comparison against prior complete months.
   - Supports the same filters as `GET /spend`: `owner`, `account_id`,
     `category`, `tag`, `tags`, `view`, `include_pending`.
   ```

#### `skills/athena-ledger/checklists/query_playbooks.md`

Add a new playbook entry after the existing **Account-scoped spend** section:

```markdown
### 7) Month-over-month trends

1. `GET /spend/trends` with `?months=<n>` (default 6).
2. Note which buckets have `partial: true` (current month) — exclude from
   comparisons or call out explicitly.
3. To narrow the trend to a subset, add the same filters used in
   `GET /spend`: `owner`, `account_id`, `category`, `tag`.
4. To validate a specific month's total, cross-check with
   `GET /spend?start_date=<YYYY-MM-01>&end_date=<YYYY-MM-last-day>`
   using matching filters — the numbers must agree.
```

### Done when

- Quality gate passes.
- `GET /spend/trends` appears in the auto-generated OpenAPI spec at
  `/openapi.json`.
- `GET /spend/trends?months=6` returns exactly 6 buckets, oldest first,
  with `partial: true` on the current month.
- All filter parameters (`owner`, `tags`, `category`, `tag`, `account_id`,
  `view`, `include_pending`) narrow results consistently with `GET /spend`.
- Months with no qualifying transactions appear as zero-filled buckets.
- `?months=0` and `?months=-1` return HTTP 422.
- Athena's `SKILL.md` lists `GET /spend/trends` in the approved API calls
  and documents it under Core analysis workflows.
- Athena's `query_playbooks.md` includes the month-over-month trends playbook.
- Hestia's skill docs are unchanged.

---

## Acceptance criteria for Sprint 18

- `GET /spend/trends` returns a plain JSON array of monthly bucket objects,
  oldest first, exactly `months` entries.
- Each bucket: `month` (YYYY-MM), `total_spend` (float), `transaction_count`
  (int), `partial` (bool).
- Current month always has `partial: true`; all prior months `partial: false`.
- Months with no matching transactions appear with zeroes (never omitted).
- All seven filter parameters from `GET /spend` are supported and produce
  results directly comparable to a matching point-in-time `GET /spend` call.
- `months` minimum is 1; no upper bound; default is 6.
- Endpoint requires bearer-token auth; unauthenticated requests return 401.
- All quality-gate commands pass.

## Explicitly deferred (out of scope for Sprint 18)

- Hestia skill doc updates (`GET /spend/trends` is not in Hestia's approved
  call list).
- `ledger doctor --fix` auto-remediation (M18 scope).
- Splitting `tests/test_server.py` into focused modules (deferred per ROADMAP
  until file exceeds ~2 000 lines or agent context pressure recurs).
