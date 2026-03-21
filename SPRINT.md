# Sprint 17 — M15: Account Labels & Enriched Spend Queries

## Sprint goal

Give account IDs human-readable identity and expand spend filtering so agents
and operators no longer maintain manual ID-to-name mappings out of band.

## Why this sprint exists

Post-M14 production use revealed three concrete gaps:

1. Plaid account IDs are opaque numbers. Agents and operators must track a
   mental (or TOOLS.md) mapping from ID to institution/purpose. (BUG-005)
2. `GET /spend` cannot be scoped to a single account. Per-card breakdowns
   require manual summation across filtered transaction pages. (BUG-008)
3. `GET /spend` cannot be scoped by category or tag. Category/tag rollups
   require full transaction pagination and client-side summing. (BUG-009)

## Working agreements

- Each task ships as its own PR; Tasks 1 and 2 are independent and can be
  developed and merged in parallel. Task 3 (skill docs) can be written in
  parallel but should be reviewed against the merged API before merging.
- All Python changes must pass the quality gate before merge:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Script/doc-only tasks (Task 3) still run the gate to confirm no regressions.
- Mark completed tasks `✅ DONE` in this file before committing.

---

## Task 1: BUG-005 — Account labels (GET /accounts + PUT /accounts/{account_id}) ✅ DONE

### Background

Agents and operators working with transaction data need to map Plaid account
IDs (e.g. `acc_abc123`) to human context (e.g. "Alice Joint Checking — Chase").
There is currently no endpoint or store for this. Agent docs explicitly call out
this gap. This task adds the store and two endpoints.

### Design decisions

**Schema:** The project does not use Alembic. The existing pattern for schema
is `CREATE TABLE IF NOT EXISTS` in `schema.sql` (idempotent) plus `ALTER TABLE`
inline in `initialize_database()` for post-deployment column additions. A brand
new table belongs in `schema.sql`. No `ALTER TABLE` or Alembic setup needed.

**Column naming:** The new table is named `account_labels` with a `label`
column (not `name`) to avoid confusion with `accounts.name` (the Plaid-sourced
account name). Both columns exist simultaneously in joined responses.

**`PUT` validation:** `PUT /accounts/{account_id}` returns HTTP 404 if the
`account_id` does not exist in the `accounts` table. This enforces the
invariant that only accounts the sync engine has seen can be labelled.
Pre-labelling an unknown account is not supported.

**`PUT` response:** Returns the full account record (same shape as one row
from `GET /accounts`), consistent with the M14 pattern where `PUT
/annotations/{transaction_id}` returns the full transaction record.

### Scope

#### `schema.sql` — new table

```sql
CREATE TABLE IF NOT EXISTS account_labels (
    id INTEGER PRIMARY KEY,
    plaid_account_id TEXT NOT NULL UNIQUE,
    label TEXT,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

No migration entry needed in `initialize_database()` because
`CREATE TABLE IF NOT EXISTS` is already idempotent.

#### `db.py` — new dataclass and query functions

**`AccountLabelRow`** — frozen dataclass for SQL binding:

```python
@dataclass(frozen=True)
class AccountLabelRow:
    plaid_account_id: str
    label: str | None
    description: str | None
    created_at: str
    updated_at: str
```

**`get_all_accounts(conn) -> list[dict[str, object]]`**

Returns all rows from `accounts` LEFT JOIN `account_labels` on
`plaid_account_id`. Each row dict must include:

| Key | Source |
|-----|--------|
| `account_id` | `accounts.plaid_account_id` |
| `plaid_name` | `accounts.name` |
| `mask` | `accounts.mask` |
| `type` | `accounts.type` |
| `subtype` | `accounts.subtype` |
| `institution_name` | `accounts.institution_name` |
| `owner` | `accounts.owner` |
| `item_id` | `accounts.item_id` |
| `canonical_account_id` | `accounts.canonical_account_id` |
| `label` | `account_labels.label` (null if no label row) |
| `description` | `account_labels.description` (null if no label row) |

Order by `accounts.plaid_account_id ASC`.

**`get_account(conn, plaid_account_id: str) -> dict[str, object] | None`**

Same join and shape as `get_all_accounts()` but restricted to a single
`plaid_account_id`. Returns `None` if the account does not exist in `accounts`.

**`upsert_account_label(conn, row: AccountLabelRow) -> None`**

Upserts into `account_labels` keyed on `plaid_account_id`. On conflict,
updates `label`, `description`, and `updated_at`.

#### `server.py` — two new endpoints

Both require standard bearer-token auth (same as all other endpoints).

---

**`GET /accounts`**

Returns all known accounts joined with any available label data.

Response shape:

```json
{
  "accounts": [
    {
      "account_id": "acc_abc123",
      "plaid_name": "Plaid Checking",
      "mask": "1234",
      "type": "depository",
      "subtype": "checking",
      "institution_name": "bank-alice",
      "owner": "alice",
      "item_id": "item-alice-001",
      "canonical_account_id": null,
      "label": "Alice Joint Checking",
      "description": "Primary joint household account"
    },
    {
      "account_id": "acc_def456",
      "plaid_name": "Plaid Savings",
      "mask": "5678",
      "type": "depository",
      "subtype": "savings",
      "institution_name": "bank-alice",
      "owner": "alice",
      "item_id": "item-alice-001",
      "canonical_account_id": null,
      "label": null,
      "description": null
    }
  ]
}
```

- Empty array if no accounts have been synced yet.
- `label` and `description` are `null` for unlabelled accounts (absent
  `account_labels` row); this is not an error.
- `canonical_account_id` is non-null only for suppressed accounts (M9
  precedence feature); include it so callers can identify the canonical
  account that takes precedence.
- No pagination required — a household will have ≤ ~20 accounts.

---

**`PUT /accounts/{account_id}`**

Upserts label data for a given Plaid account ID.

Request body:

```json
{
  "label": "Alice Joint Checking",
  "description": "Primary joint household account"
}
```

Both fields are optional (either or both may be null/omitted). Sending null
for a field clears its value in the store.

Response — HTTP 200 with the full account record:

```json
{
  "account_id": "acc_abc123",
  "plaid_name": "Plaid Checking",
  "mask": "1234",
  "type": "depository",
  "subtype": "checking",
  "institution_name": "bank-alice",
  "owner": "alice",
  "item_id": "item-alice-001",
  "canonical_account_id": null,
  "label": "Alice Joint Checking",
  "description": "Primary joint household account"
}
```

HTTP 404 if `account_id` does not exist in the `accounts` table.

**Pydantic request model (`AccountLabelRequest`):**

```python
class AccountLabelRequest(BaseModel):
    label: str | None = None
    description: str | None = None
```

#### `tests/test_server.py` — new test class

Add a `TestAccountsEndpoints` class covering:

- `GET /accounts` returns HTTP 200 with the full accounts list when accounts
  have been synced.
- `GET /accounts` returns `{"accounts": []}` when the database is empty.
- `GET /accounts` includes `label` and `description` as null for unlabelled
  accounts.
- `GET /accounts` includes `label` and `description` from `account_labels`
  when a label row exists.
- `GET /accounts` requires auth (unauthenticated request returns 401).
- `PUT /accounts/{account_id}` returns HTTP 200 with the full account record
  after writing label data.
- The returned record from `PUT` contains the values just written.
- A second `PUT` (update, not create) returns the newly updated label fields.
- `PUT /accounts/{account_id}` returns HTTP 404 for an unknown account ID.
- `PUT /accounts/{account_id}` requires auth (unauthenticated → 401).
- `PUT /accounts/{account_id}` with null fields clears label/description.

**Test data guidance:** Seed the database with at least two accounts (using
`initialize_database()` + direct `sqlite3` inserts into `accounts`), and
seed an `account_labels` row for one of them to test the mixed
labelled/unlabelled case.

### Done when

- Quality gate passes.
- `GET /accounts` and `PUT /accounts/{account_id}` appear in the
  auto-generated OpenAPI spec at `/openapi.json`.
- An agent can call `GET /accounts` to get the full household account list
  with labels, then use the `account_id` values in subsequent API calls.
- Labelling an account does not affect sync data in `accounts`.

---

## Task 2: BUG-008 + BUG-009 — GET /spend enriched filters (account_id, category, tag)

### Background

`GET /spend` aggregates across the entire ledger with no way to scope by
account, category, or tag. Three separate bugs (BUG-008, BUG-009) cover these
gaps. They are combined into one task because they modify the same dataclass
(`SpendQuery`), the same query function (`query_spend()`), and the same
endpoint handler (`get_spend()`) — implementing them separately would produce
merge conflicts.

### Design decisions

**`account_id` filter:** Applied directly as `t.plaid_account_id = ?` without
requiring an additional JOIN (the `accounts` join is already conditional and
should remain so).

**`category` filter:** Case-insensitive match against `ann.category`. The
`annotations` LEFT JOIN is already present in `query_spend()`. Use
`LOWER(ann.category) = LOWER(?)` or `ann.category = ? COLLATE NOCASE` —
either is acceptable, but be consistent with how the `category` filter is
implemented in `get_distinct_categories()` (which uses `COLLATE NOCASE` for
ordering; either approach is fine).

**`tag` filter (singular, new):** Case-insensitive match against individual
tag values in the `ann.tags` JSON array. Use:

```sql
EXISTS (
    SELECT 1 FROM json_each(ann.tags)
    WHERE LOWER(value) = LOWER(?)
)
```

This is a new singular `tag` parameter distinct from the existing plural
`tags` multi-value parameter. Both coexist and are AND-combined.

**Why singular `tag` instead of extending `tags`?** The ROADMAP explicitly
specifies `category` and `tag` (singular) as new parameters for BUG-009.
A singular `tag` is ergonomic for agents making simple one-tag scoped spend
queries without list syntax. The existing `tags` multi-value parameter
(AND semantics) remains unchanged.

**`filters` response field:** Extend the `filters` key in the `GET /spend`
response to surface the new parameters, so callers can confirm what filters
were applied:

```json
{
  "filters": {
    "owner": null,
    "tags": [],
    "account_id": "acc_abc123",
    "category": "software",
    "tag": "recurring"
  }
}
```

All four filter keys are always present in the response (null/empty when not
supplied), matching the existing pattern for `owner` and `tags`.

**AND semantics when multiple filters are combined:** When `account_id`,
`category`, `tag`, and `tags` are all provided, they are all ANDed together
(narrow to the intersection). This is consistent with the existing `tags`
multi-value behavior.

**`S608` noqa note:** `query_spend()` already carries a `# noqa: S608`
comment explaining why fragment interpolation is unavoidable there. The same
rationale applies to any new fragments added in this task — extend the
existing comment to cover them rather than adding new noqa lines.

### Scope

#### `db.py` — extend `SpendQuery` and `query_spend()`

**Extend `SpendQuery`:**

```python
@dataclass(frozen=True)
class SpendQuery:
    start_date: str
    end_date: str
    owner: str | None = None
    tags: tuple[str, ...] = ()
    include_pending: bool = False
    canonical_only: bool = True
    account_id: str | None = None   # new — BUG-008
    category: str | None = None     # new — BUG-009
    tag: str | None = None          # new — BUG-009 (singular, case-insensitive)
```

**Extend `query_spend()`:**

Add three new conditional WHERE clauses:

1. `account_id` filter (no additional JOIN required):

   ```python
   if query.account_id is not None:
       where_parts.append("t.plaid_account_id = ?")
       params.append(query.account_id)
   ```

2. `category` filter (case-insensitive; `annotations_join` is already present):

   ```python
   if query.category is not None:
       where_parts.append("LOWER(ann.category) = LOWER(?)")
       params.append(query.category)
   ```

3. `tag` filter (singular, case-insensitive; `annotations_join` already present):

   ```python
   if query.tag is not None:
       where_parts.append(
           "EXISTS ("
           "SELECT 1 FROM json_each(ann.tags) WHERE LOWER(value) = LOWER(?)"
           ")"
       )
       params.append(query.tag)
   ```

#### `server.py` — extend `SpendListQuery` and `get_spend()`

**Extend `SpendListQuery`:**

```python
class SpendListQuery(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    owner: str | None = None
    include_pending: bool | None = None
    view: Literal["canonical", "raw"] = "canonical"
    account_id: str | None = None   # new — BUG-008
    category: str | None = None     # new — BUG-009
    tag: str | None = None          # new — BUG-009
```

**Extend `get_spend()` — wire up new filters and update `filters` response:**

Pass `account_id`, `category`, and `tag` to `SpendQuery`. Extend the returned
`filters` dict:

```python
return {
    "start_date": resolved_start.isoformat(),
    "end_date": resolved_end.isoformat(),
    "total_spend": total_spend,
    "transaction_count": transaction_count,
    "includes_pending": include_pending,
    "filters": {
        "owner": params.owner,
        "tags": resolved_tags,
        "account_id": params.account_id,
        "category": params.category,
        "tag": params.tag,
    },
}
```

#### `tests/test_server.py` — additions to `TestGetSpendEndpoint`

Add tests to the existing `TestGetSpendEndpoint` class (or a new
`TestGetSpendEnrichedFilters` class if the existing class is already long).
Test data must include accounts and annotations to exercise the new filters.

Minimum required tests:

- `?account_id=<id>` restricts spend to transactions for that account.
- `?account_id=<unknown_id>` returns zero spend (no error).
- `?category=software` restricts spend to transactions annotated with that
  category (case-insensitive: `Software` and `software` both match).
- `?tag=recurring` restricts spend to transactions tagged with that value
  (case-insensitive: `Recurring` and `recurring` both match).
- `?category=software&tag=recurring` applies both filters (AND semantics);
  only transactions matching both are counted.
- `?account_id=<id>&category=software` applies both (AND semantics).
- All three filters absent → unchanged behavior (existing tests must still
  pass unchanged).
- Response `filters` field always includes `account_id`, `category`, and `tag`
  keys, even when null/not supplied.

### Done when

- Quality gate passes.
- `GET /spend?account_id=<id>` returns spend scoped to that account.
- `GET /spend?category=software` returns spend scoped to that category
  (case-insensitive).
- `GET /spend?tag=recurring` returns spend scoped to that tag
  (case-insensitive).
- All three filters are AND-combinable with each other and with the existing
  `owner`, `tags`, and `range` parameters.
- Response `filters` field includes `account_id`, `category`, and `tag`.

---

## Task 3: Skill doc updates for M15

### Background

Per the ROADMAP, every new or changed endpoint that agents may call must be
reflected in both skill bundles before the milestone is considered done.

### Scope

**`skills/hestia-ledger/SKILL.md`**

1. Add `GET /accounts` and `PUT /accounts/{account_id}` to the
   **Approved API calls** list:

   ```
   6. `GET /accounts` — retrieve all known accounts with human-readable labels
   7. `PUT /accounts/{account_id}` — write or update a label for an account
   ```

2. Add a note under the **API guardrails** section (or as a new
   "Account identity" guardrail) that Hestia should call `GET /accounts` when
   it needs to identify an account by name, and `PUT /accounts/{account_id}`
   to apply a label when the operator has provided one.

**`skills/athena-ledger/SKILL.md`**

1. Add `GET /accounts` and `PUT /accounts/{account_id}` to the
   **Approved API calls** list:

   ```
   7. `GET /accounts` — retrieve all known accounts with human-readable labels
   8. `PUT /accounts/{account_id}` — write or update a label for an account
   ```

2. Update the description of `GET /spend` under **Core analysis workflows → 2)
   Spend rollups** to document the three new optional filters:

   ```
   Optional narrowing filters (AND-combined with each other and with owner/tags):
   - `account_id` — restrict to one account (use `GET /accounts` to discover IDs)
   - `category` — restrict to one annotation category (case-insensitive;
     use `GET /categories` for vocabulary)
   - `tag` — restrict to one annotation tag (case-insensitive, singular;
     use `GET /tags` for vocabulary)
   ```

**`skills/athena-ledger/checklists/query_playbooks.md`**

1. Add a new playbook entry after the existing "Period spend summary" playbook:

   ```markdown
   ### 6) Account-scoped spend

   1. `GET /accounts` to list all known accounts with labels.
   2. Identify the target `account_id` from the response.
   3. `GET /spend` with `account_id=<id>` and desired date window.
   4. Optionally narrow further with `category` or `tag` filters.
   ```

2. In the **"Period spend summary"** playbook (playbook 1), add a note that
   spend can be narrowed with `account_id`, `category`, or `tag`:

   ```
   Optional: add `account_id`, `category`, or `tag` to narrow the aggregation.
   ```

**`skills/hestia-ledger/checklists/query_playbooks.md`**

1. Add a brief note to the **Vocabulary setup (start of run)** section:

   ```
   3. `GET /accounts` — retrieve account ID-to-label mapping for any
      account-specific annotation context.
   ```

### Done when

- Quality gate passes.
- Both `SKILL.md` files list `GET /accounts` and `PUT /accounts/{account_id}`
  in their approved API calls sections.
- Athena's `SKILL.md` documents `account_id`, `category`, and `tag` as new
  `GET /spend` filter parameters.
- Athena's `query_playbooks.md` includes the account-scoped spend playbook.
- Hestia's `query_playbooks.md` includes `GET /accounts` in vocabulary setup.

---

## Acceptance criteria for Sprint 17

- `GET /accounts` returns all synced accounts with label data where available.
- `PUT /accounts/{account_id}` upserts a label and returns the full account
  record; returns 404 for unknown account IDs.
- `GET /spend?account_id=<id>` restricts aggregation to a single account.
- `GET /spend?category=<value>` restricts aggregation to one category
  (case-insensitive).
- `GET /spend?tag=<value>` restricts aggregation to one tag (case-insensitive).
- All new filters are AND-combinable with each other and with existing filters.
- Response `filters` field in `GET /spend` includes all new parameters.
- Both skill bundles document the new endpoints and updated `GET /spend` params.
- All quality-gate commands pass on every merged PR.

## Explicitly deferred (out of scope for Sprint 17)

- `GET /spend/trends` month-over-month endpoint (M16 scope).
- `ledger doctor --fix` auto-remediation (M18 scope).
- Inlining `account_name` into `GET /transactions` or `GET /spend` responses
  (BUGS.md notes this as TBD; keeping it join-only on `GET /accounts` for now
  to avoid response bloat).
- Pagination for `GET /accounts` (household account counts are small; revisit
  if a multi-institution setup exceeds ~50 accounts).
