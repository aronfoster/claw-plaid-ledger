---
name: athena-ledger
description: Analyse claw-plaid-ledger transactions. Use for spend rollups, anomaly review, owner-aware summaries, and targeted annotation of needs-athena-review items. Reads from the ledger HTTP API using bearer-token auth.
metadata:
  openclaw:
    emoji: '📊'
    requires:
      env:
        - CLAW_API_SECRET
        - CLAW_LEDGER_URL
      config:
        - ~/.openclaw/.env
    primaryEnv: CLAW_API_SECRET
---

## Setup

```bash
cat >> ~/.openclaw/.env <<'EOF'
CLAW_API_SECRET=<your-CLAW_API_SECRET-value>
CLAW_LEDGER_URL=http://127.0.0.1:8000
EOF
chmod 600 ~/.openclaw/.env
```

`CLAW_API_SECRET` is the bearer token required on all non-health ledger API endpoints.
`CLAW_LEDGER_URL` is the base URL of the running `ledger serve` instance (default: `http://127.0.0.1:8000`).

All API calls must include:
```
Authorization: Bearer $CLAW_API_SECRET
```

The ledger server must be running before invoking this skill (`uv run --locked ledger serve`).

# Athena Ledger Analysis Skill

## Purpose

Athena is the **lower-frequency analyst** for `claw-plaid-ledger`.

Athena should:

- review transactions tagged `needs-athena-review`,
- run deterministic spend rollups using `GET /spend`,
- produce owner-aware summaries and anomaly narratives for humans,
- issue only targeted clarification annotations when analyst evidence is strong.

Athena must not:

- act as high-volume ingestion worker,
- rewrite canonical precedence behavior,
- present uncertain findings as confirmed facts.

## Boundaries and authority

- Ledger API data is the source of truth.
- `view=canonical` is default for analysis and reporting.
- `view=raw` is diagnostic-only and paired with matching canonical queries.
- Athena may annotate selectively, but bulk annotation churn belongs to Hestia.

## Approved API calls

1. `GET /transactions`
2. `GET /transactions/{id}`
3. `GET /spend`
4. `GET /categories` — discover the current allocation category vocabulary
5. `GET /tags` — discover the current allocation tag vocabulary
6. `PUT /annotations/{transaction_id}` — **compatibility shim: single-allocation
   transactions only.** Returns HTTP 409 if the transaction has been split into
   multiple allocations. For all practical purposes, use
   `PUT /transactions/{id}/allocations` instead (it handles both split and unsplit
   transactions correctly). Clarification-only, low volume.
7. `GET /accounts` — retrieve all known accounts with human-readable labels
8. `PUT /accounts/{account_id}` — write or update a label for an account
9. `GET /spend/trends` — monthly spend buckets for a lookback window;
   supports the same filters as `GET /spend`
10. `GET /errors` — recent ledger warnings and errors; use for proactive
    alerting and pre-analysis health checks
11. `PUT /transactions/{id}/allocations` — **primary write surface for
    category/tags/note.** Atomically replaces all allocations for a transaction.
    Request body is a JSON array of `{amount, category?, tags?, note?}` items.
    Amounts must sum to the transaction total (auto-corrects within $1.00; returns
    422 with `transaction_amount`, `allocation_total`, `difference` if off by more).
    Response is the full transaction detail with `"allocations": [...]`.

## Allocation response shapes

The list and detail endpoints return different shapes. Always check context.

### List view — `GET /transactions`

Each row includes `"allocation": {...}` (singular object). One row per
(transaction, allocation) pair. `category`, `tags`, and `note` may be null for
uncategorized transactions.

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

Use `allocation.category`, `allocation.tags`, `allocation.note` when working
with list results. The `allocation` key is always present (never null).

For split transactions, the same transaction `id` appears once per allocation
in list results — grouping by `id` reveals split transactions.

### Detail view — `GET /transactions/{id}` and `PUT /transactions/{id}/allocations`

These endpoints return `"allocations": [...]` (array, never null). For unsplit
transactions the array has exactly one element; for split transactions it
contains all allocations ordered by `id ASC`.

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

Use `allocations[0].category` etc. when working with detail-view results.
Check `allocations.length` to detect split transactions.

## Pagination

`GET /transactions` supports offset-based pagination via `limit` and `offset`
query parameters. Every list response includes:

```json
{
  "transactions": [...],
  "total": 247,
  "limit": 100,
  "offset": 0
}
```

- `limit` — number of rows per page; default `100`, maximum `500`.
- `offset` — zero-based row index of the first row on this page.
- `total` — total number of rows matching the query (independent of limit/offset).

**To paginate to completion:**

1. Start with `offset=0` and a fixed `limit` (e.g. `100`). Keep `limit` stable
   within a run.
2. After each response, advance: `offset += limit`.
3. Stop when `offset >= total` — the next page would be empty.

Equivalently: stop when the number of rows returned is less than `limit`
(the server returned a partial page, meaning this was the last).

**If pagination is interrupted** (call fails or run is aborted mid-way),
report partial coverage and avoid definitive completeness claims.

## Core analysis workflows

### 0) Vocabulary discovery

Before annotating, call `GET /categories` and `GET /tags` to retrieve the
current allocation vocabulary already present in the ledger. These endpoints
now draw from `allocations`, not `annotations`. This avoids creating
near-duplicate labels (e.g. `groceries` vs `grocery`).

### 1) Review `needs-athena-review` queue

1. Query `GET /transactions` with `tags=needs-athena-review` + explicit window
   (or `range` shorthand — see workflow 2).
2. Paginate to completion.
3. Each transaction in the list response includes a nested `allocation` field
   (singular object — list-view shape). It is always present (never null);
   `category`, `tags`, and `note` may be null for uncategorized transactions.
   Use `allocation.category`, `allocation.tags`, and `allocation.note` to
   triage without a round-trip to `GET /transactions/{id}`.
4. Drill into each priority record with `GET /transactions/{id}` before
   quoting amounts or allocation details in a final report. The detail
   response contains `"allocations": [...]` (array); use `allocations[0]`
   for unsplit transactions or iterate all elements for split ones.
5. Classify issue type (spike, missing expected, duplicate, mismatch,
   orphan/discrepancy).
6. Produce a human-facing assessment and next action.

### 2) Spend rollups for defined windows

Use `GET /spend` with either:

- An explicit window: `start_date` + `end_date` + `view=canonical`.
- A range shorthand: `range=this_month`, `range=last_month`,
  `range=last_30_days`, or `range=last_7_days` (server resolves dates
  automatically; resolved `start_date`/`end_date` are echoed in the
  response).

Optional narrowing filters (AND-combined with each other and with owner/tags):
- `account_id` — restrict to one account (use `GET /accounts` to discover IDs)
- `category` — restrict to one allocation category (case-insensitive;
  use `GET /categories` for vocabulary)
- `tag` — restrict to one allocation tag (case-insensitive, singular;
  use `GET /tags` for vocabulary)

Then run matching `GET /transactions` for representative evidence.
`GET /transactions` accepts the same `range` shorthands, so the evidence
query can use `range=last_month` to mirror the spend call without computing
explicit dates. List results include `allocation` data directly (no
drill-down required for initial evidence scanning).
`GET /spend` sums allocation amounts. For split transactions filtered by
category, only the matching allocation amount is summed — not the full
transaction amount. This gives accurate per-category totals even when a
single transaction is split across multiple categories.

`GET /spend` and `GET /spend/trends` responses include `allocation_count`
(not `transaction_count`) — the count reflects allocation rows, not
transaction rows.
Separate posted vs pending observations.  Report totals only for the exact
queried window (use the `start_date`/`end_date` fields in the response to
confirm the resolved window).

### 3) Owner-aware summaries

1. Run owner-scoped `GET /spend` and matching `GET /transactions`.
2. Drill into outliers with `GET /transactions/{id}` before quoting details.
3. Use the owner summary template for structured output.

### 4) Month-over-month trends

Use `GET /spend/trends` when the question involves change over time
(e.g. "is spending increasing?", "which month was the most expensive?").

- `?months=<n>` controls the lookback window (default 6, no upper bound).
- The current month is flagged `partial: true` — treat it as incomplete
  and avoid direct comparison against prior complete months.
- Supports the same filters as `GET /spend`: `owner`, `account_id`,
  `category`, `tag`, `tags`, `view`, `include_pending`.

### 5) Anomaly narrative workflow

1. Confirm candidate anomalies with canonical queries over explicit windows.
2. Use raw view only when discrepancy diagnosis is needed.
3. Assign confidence (`high`, `medium`, `low`) and uncertainty sources.
4. Provide follow-up actions with clear operator ownership.

### 6) Proactive error alerting

Call `GET /errors` when preparing a periodic summary or when a user asks
about ledger health. Useful parameters:

- `?hours=24` — last day (default)
- `?hours=168` — last week
- `?min_severity=ERROR` — errors only (exclude warnings)

If ERROR-level rows are present, include them in the summary and recommend
the operator investigate. Warnings may be informational — note them but
do not escalate unless they form a pattern.

## Allocation write policy (Athena)

Athena allocation writes are optional and minimal. Only write when:

- clarification materially improves future review,
- transaction was re-fetched in the current run (`GET /transactions/{id}`),
- evidence is specific and confidence is at least medium.

**Pre-flight check before any write:**

1. Re-fetch the transaction with `GET /transactions/{id}`.
2. Check `allocations.length` in the response.
3. Use `PUT /transactions/{id}/allocations` for all writes — it handles both
   unsplit (`allocations.length == 1`) and split (`allocations.length > 1`)
   transactions correctly.
4. Do **not** call `PUT /annotations/{id}` on split transactions — it returns
   HTTP 409.

When uncertain, keep or add `needs-athena-review` tag and document unresolved
questions instead of guessing.

## Reviewing split transactions

A transaction split by an operator appears once per allocation in
`GET /transactions` list results. To identify split transactions:

1. In list results, group rows by transaction `id`. Any `id` that appears more
   than once is a split transaction; each row carries one allocation's category,
   amount, and tags.
2. Drill into `GET /transactions/{id}` to see all allocations together in the
   `"allocations": [...]` array.
3. For spend rollups: `GET /spend?category=groceries` correctly sums only
   grocery allocation amounts — not the full transaction amount — so category
   totals are accurate even for split transactions.
4. Do not attempt to overwrite an operator-defined split unless explicitly
   instructed. Flag unusual splits for operator review.

## Output contract

Athena responses are human-facing analysis with this structure:

1. Query frame (window, filters, view, coverage)
2. Findings (rollups and/or anomaly sections)
3. Confidence and uncertainty
4. Recommended follow-up actions

## Companion files

- `checklists/query_playbooks.md`
- `checklists/anomaly_review_flow.md`
- `templates/owner_summary_template.md`
- `templates/anomaly_review_template.md`
