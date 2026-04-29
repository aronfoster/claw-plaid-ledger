# Sprint 29 — Multi-category Filtering (M26)

## Sprint goal

Let agents and operators filter allocation-aware reads by one or more
categories without client-side post-filtering. Category filters must operate on
allocation rows, not whole transactions, so split transactions only return the
matching allocation portions.

## Background

The ledger is now allocation-first. `GET /spend` and `GET /spend/trends`
already support a single `category` filter, but callers cannot ask for several
categories in one request. `GET /transactions` and `GET /transactions/splits`
return allocation-aware rows, but they do not currently expose any category
filter at all.

This forces Athena and operators to fetch broad transaction sets and perform
manual client-side filtering for questions like "show groceries or dining for
last month". That is slow, error-prone, and especially confusing for split
transactions where only one allocation row may match the requested categories.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **Repeated `category` query params express OR semantics.** Examples:
  `?category=groceries` and `?category=groceries&category=dining`.
- **Existing single-category calls keep working.** A caller using exactly one
  `category` value should see the same category-filtered result semantics as
  before on endpoints that already supported `category`.
- **Category matching is case-insensitive**, matching the existing
  `GET /spend?category=...` behavior.
- **Filtering is per allocation row.** If a transaction is split across
  `groceries` and `dining`, a `category=groceries` query returns only the
  groceries allocation row. The unrelated dining allocation row must not appear
  merely because it shares the same transaction ID.
- **Multiple categories use OR semantics.** An allocation matches when its
  category equals any requested category.
- **Uncategorized allocations do not match named category filters.** A category
  filter must implicitly exclude `NULL` categories.
- **`GET /transactions/uncategorized` rejects category filters.** A request such
  as `/transactions/uncategorized?category=groceries` returns HTTP 422 because
  the endpoint means `allocation.category IS NULL` and cannot also match named
  categories.
- **No comma-delimited category parsing.** Category names are passed as repeated
  query params only.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before the
  next starts.
- Mark completed tasks `✅ DONE` before committing.
- Do not add lint/type/test bypasses. If one is truly unavoidable, follow the
  strict bypass policy in `AGENTS.md` and report it explicitly.

---

## Task 1: Add allocation-category filter support in the DB layer ✅ DONE

### What

Add reusable multi-category filtering to the read query layer so every API
surface can rely on identical allocation-row semantics.

### Requirements

- Extend `TransactionQuery`, `SpendQuery`, and `SpendTrendsQuery` to accept a
  tuple of requested categories rather than a single scalar-only category.
- Preserve current single-category behavior for spend queries by representing
  one requested category as a one-item tuple.
- Add a small shared helper for category predicates if it keeps the SQL
  consistent and avoids duplication.
- Category predicates must be case-insensitive.
- Category predicates must use OR semantics across requested categories.
- Category predicates must match `alloc.category`, not transaction-level data.
- Category predicates must exclude `NULL` categories naturally.
- Keep tag filtering behavior unchanged: repeated `tags` still means AND, and
  singular `tag` still means the existing case-insensitive tag match.

### Split-transaction examples for tests

Use generic, non-private fixture values only:

- Transaction `tx-split-1` has two allocations:
  `amount=60.00, category="groceries"` and
  `amount=40.00, category="dining"`.
- `category=groceries` returns only the groceries allocation row.
- `category=dining` returns only the dining allocation row.
- `category=groceries&category=dining` returns both allocation rows.
- `category=utilities` returns neither allocation row.

### Tests

- DB tests for `query_transactions()` confirming per-allocation filtering and
  `total` counts only matching allocation rows.
- DB tests for `query_spend()` confirming sums and `allocation_count` include
  only matching allocations across one and multiple categories.
- DB tests for `query_spend_trends()` confirming monthly buckets sum only
  matching allocations and preserve zero-fill behavior.
- Regression test that a single category produces the same spend result as the
  previous scalar behavior.

### Done when

- DB query objects can represent zero, one, or multiple requested categories.
- All category-aware DB reads apply the same allocation-row OR predicate.
- Split transactions do not leak unrelated allocation rows into filtered
  results.
- Full quality gate passes.

---

## Task 2: Add repeated `category` filtering to transaction read endpoints

### What

Expose category filtering on transaction-style allocation reads while rejecting
category filters on the dedicated uncategorized queue.

### Endpoints in scope

- `GET /transactions`
- `GET /transactions/splits`
- `GET /transactions/uncategorized`

### Requirements

- `GET /transactions` accepts repeated `category` query params:
  `/transactions?category=groceries&category=dining`.
- `GET /transactions/splits` accepts the same repeated `category` query params.
- Both endpoints pass all supplied categories to the DB layer as a tuple.
- `GET /transactions/uncategorized` does **not** accept `category`; strict query
  parameter checking must return HTTP 422 with the existing
  `unrecognized` / `valid_parameters` response shape.
- Update strict-parameter allowlists so `category` is valid on `/transactions`
  and `/transactions/splits`, but invalid on `/transactions/uncategorized`.
- Pagination metadata (`total`, `limit`, `offset`) must reflect the filtered
  allocation-row result set.
- Existing filters (`range`, dates, account, pending, amount, keyword, notes,
  tags, view, pagination) must continue to combine with category filters using
  AND semantics.

### Tests

- `GET /transactions?category=groceries` returns only matching allocation rows.
- `GET /transactions?category=groceries&category=dining` returns the union of
  matching allocation rows.
- `GET /transactions/splits?category=groceries` returns only matching split
  allocations, not all allocations from the same transaction.
- `GET /transactions/uncategorized?category=groceries` returns HTTP 422 and
  reports `category` as unrecognized.
- Existing unfiltered transaction and uncategorized endpoint tests continue to
  pass unchanged.

### Done when

- Transaction-style reads support repeated named categories where appropriate.
- The uncategorized queue rejects contradictory category filters.
- Full quality gate passes.

---

## Task 3: Add repeated `category` filtering to spend endpoints

### What

Upgrade aggregate spend surfaces from single-category filtering to repeated
category filtering with identical OR semantics.

### Endpoints in scope

- `GET /spend`
- `GET /spend/trends`

### Requirements

- Both endpoints accept repeated `category` query params.
- `GET /spend?category=groceries` keeps the existing single-category behavior.
- `GET /spend?category=groceries&category=dining` returns the sum and
  `allocation_count` for allocations whose category is groceries OR dining.
- `GET /spend/trends` applies the same category filter before grouping by
  month.
- Existing filters (`start_date`, `end_date`, `range`, `owner`, `tags`, `tag`,
  `account_id`, `view`, `include_pending`, and `months` where applicable) keep
  their current behavior and combine with categories using AND semantics.
- Response filter echoing must remain useful for agents. At minimum, the
  response should expose the requested category list in a predictable field so
  agents can confirm what was applied.
- Update strict-parameter allowlists only if needed; the public parameter name
  remains `category`.

### Tests

- `GET /spend` with one category matches existing behavior.
- `GET /spend` with two categories sums only those matching allocation rows.
- `GET /spend/trends` with two categories groups only matching allocation rows.
- Combining category with `owner`, `account_id`, `tag`, and `tags` remains AND
  semantics outside the category OR group.
- Invalid or misspelled parameters still return the existing strict-params 422.

### Done when

- Spend and trend queries support one or more repeated categories.
- Aggregates and counts are allocation-based and category-filtered.
- Full quality gate passes.

---

## Task 4: Update API documentation and skill guidance

### What

Update user-facing and agent-facing documentation so Hestia and Athena know how
to use repeated category filters and do not perform unnecessary client-side
post-filtering.

### Documentation in scope

- `README.md` if it documents API filters.
- `RUNBOOK.md` if it documents API/operator query usage.
- `ARCHITECTURE.md` if it describes allocation-query semantics.
- `skills/hestia-ledger/SKILL.md`
- `skills/hestia-ledger/checklists/query_playbooks.md`
- `skills/athena-ledger/SKILL.md`
- `skills/athena-ledger/checklists/query_playbooks.md`
- Any other markdown file that references category-filter behavior.

### Requirements

- Document repeated `category` parameters with examples:
  `ledger-api "/spend?range=last_month&category=groceries&category=dining"`.
- Explain that multiple categories use OR semantics.
- Explain that other filters combine with the category group using AND
  semantics.
- Explain that category filtering is allocation-row based, so split
  transactions only return or count matching allocations.
- Explain that `GET /transactions/uncategorized` is the correct workflow for
  uncategorized allocations and rejects named category filters.
- Remove or update any guidance that tells agents to fetch a broad set and
  filter categories client-side when the server can now do it.
- Preserve Hestia/Athena responsibility boundaries: Hestia primarily uses
  transaction queues for ingestion; Athena owns spend analysis unless explicitly
  asked otherwise.

### Done when

- Docs and skill bundles describe multi-category filtering consistently.
- Agents have copy-ready examples for transaction, spend, and trend queries.
- No markdown file describes single-category-only behavior where multi-category
  behavior now exists.
- Full quality gate passes.

---

## Task 5: Close M26 in roadmap after implementation

### What

After Tasks 1–4 are complete, mark M26 complete in `ROADMAP.md` and ensure the
sprint board reflects completion.

### Requirements

- Move `M26 — Multi-category Filtering` from Upcoming Milestones to Completed
  Milestones.
- Summary should match the style of prior completed entries and mention:
  repeated `category` query params, OR semantics, allocation-row filtering,
  split-transaction behavior, spend/trend parity, and skill/doc updates.
- Leave `M27 — Plaid Required Attestations` as the next upcoming milestone.
- Mark this task and all prior sprint tasks `✅ DONE` in `SPRINT.md` before any
  commit.

### Done when

- `ROADMAP.md` accurately reflects M26 completion.
- `SPRINT.md` has all Sprint 29 tasks marked `✅ DONE`.
- Full quality gate passes.

---

## Acceptance criteria for Sprint 29

- A caller can pass one `category` and receive the same kind of results as
  before on endpoints that already supported category filtering.
- A caller can pass repeated `category` params and receive rows or aggregates
  matching any requested category.
- `GET /transactions` supports repeated category filters.
- `GET /transactions/splits` supports repeated category filters.
- `GET /transactions/uncategorized` rejects named category filters with HTTP
  422.
- `GET /spend` and `GET /spend/trends` use the same category semantics as
  transaction-style reads.
- Split transactions never cause unrelated allocations to appear in filtered
  transaction results or contribute to filtered aggregates.
- Uncategorized allocations are excluded from named-category results.
- Documentation and skill guidance explain the behavior clearly enough that
  agents do not need to post-filter categories client-side.
- Full quality gate (`uv run --locked ruff format . --check`,
  `uv run --locked ruff check .`, `uv run --locked mypy .`,
  `uv run --locked pytest -v`) passes with no regressions.
