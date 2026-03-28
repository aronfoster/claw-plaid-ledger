# Sprint 24 — M22: On-demand Plaid Refresh

## Sprint goal

Give operators a CLI command (`ledger refresh`) to trigger an immediate Plaid
transaction refresh. This tells Plaid to re-check the institution and fire a
`SYNC_UPDATES_AVAILABLE` webhook to the registered URL, letting operators
confirm end-to-end webhook delivery in production without raw HTTP tooling.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **`refresh_transactions` returns `None`** — The Plaid `/transactions/refresh`
  endpoint returns HTTP 200 with an empty response body. The adapter method is
  fire-and-forget; it raises on error and returns `None` on success. No internal
  model translation is needed.
- **Error handling mirrors `sync_transactions`** — HTTP 429 and 5xx responses
  raise `PlaidTransientError`; other 4xx responses raise `PlaidPermanentError`.
  `OSError` raises `PlaidTransientError`. Both error types inherit from
  `PlaidSyncError(RuntimeError)`, so the `--all` loop can catch `RuntimeError`
  consistently.
- **CLI output is minimal** — Refresh is a trigger, not a data operation. One
  confirmation line per item is sufficient; no summary table required.
  - Default and `--item` modes: `refresh[<item_id>]: OK` on success,
    `refresh[<item_id>]: ERROR <message>` on failure (exit 1).
  - Default mode (no item): `refresh: OK` / `refresh: ERROR <message>`.
  - `--all` mode: one line per item, then
    `refresh --all: N items refreshed, M failed` (exit 1 if M > 0).
- **Mutual exclusion and missing-config exits** — `--item` and `--all` together
  exits 2. Missing required config exits 2. Adapter errors exit 1. These match
  the exit-code conventions of `ledger sync`.
- **Default mode does not inspect items.toml** — `_refresh_default_mode()`
  calls `load_config(require_plaid=True)` and requires `PLAID_ACCESS_TOKEN`.
  If items.toml is present but `PLAID_ACCESS_TOKEN` is not set, the command
  fails with exit 2 and a missing-variable message. It does **not** auto-select
  the first item from items.toml. Operators on multi-item setups must use
  `--all` or `--item`. This is identical to `_sync_default_mode()` behaviour.
- **Default mode uses `PLAID_ACCESS_TOKEN` singleton** — identical to
  `ledger sync` default mode: calls `load_config(require_plaid=True)` and uses
  `config.plaid_access_token`.
- **No doctor integration, no skill updates** — `ledger refresh` is a CLI
  operator tool only. No API endpoint is added; agents do not need it.
- **Sandbox behavior is transparent** — `/transactions/refresh` is available in
  both sandbox and production environments. The adapter makes no environment
  check; behavior differences (real webhook delivery vs. sandbox simulation) are
  Plaid-side.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before the
  next starts.
- No schema changes, no API changes, no skill bundle changes.
- Mark completed tasks `✅ DONE` before committing.

---

## Task 1: `PlaidClientAdapter.refresh_transactions` ✅ DONE

### What

Add a single new method to `PlaidClientAdapter` in `plaid_adapter.py`. This is
the only Plaid SDK boundary for the refresh feature.

### Method signature

```python
def refresh_transactions(self, access_token: str) -> None:
    """
    Ask Plaid to re-check the institution and fire SYNC_UPDATES_AVAILABLE.

    Calls the Plaid /transactions/refresh endpoint.  Returns None on success
    (Plaid returns an empty 200 body).  Raises PlaidTransientError for
    rate-limit and server errors; PlaidPermanentError for all other API
    errors (e.g. INVALID_ACCESS_TOKEN); PlaidTransientError for network
    errors.
    """
```

### SDK import

Add alongside the existing transaction-related imports:

```python
from plaid.model.transactions_refresh_request import (  # type: ignore[import-untyped]
    TransactionsRefreshRequest,
)
```

### Implementation

Pattern is identical to `update_item_webhook` (fire-and-forget, no response
model translation needed):

```python
request = TransactionsRefreshRequest(access_token=access_token)
try:
    self._api.transactions_refresh(request)
except plaid.ApiException as exc:
    status: int = getattr(exc, "status", 0)
    if (
        status == _HTTP_TOO_MANY_REQUESTS
        or status >= _HTTP_SERVER_ERROR_MIN
    ):
        msg = f"Plaid transient API error (HTTP {status}): {exc}"
        raise PlaidTransientError(msg) from exc
    msg = f"Plaid permanent API error (HTTP {status}): {exc}"
    raise PlaidPermanentError(msg) from exc
except OSError as exc:
    msg = f"Network error calling Plaid: {exc}"
    raise PlaidTransientError(msg) from exc
```

### Tests

Add to an existing or new adapter test file (e.g., `tests/test_plaid_adapter.py`
if it exists, or the appropriate test file that covers adapter methods).

- **Success path** — mock `self._api.transactions_refresh` to return any
  truthy value; assert the method returns `None` and the SDK was called once
  with an `access_token` matching the input.
- **Transient error — rate limit (HTTP 429)** — mock `transactions_refresh` to
  raise `plaid.ApiException` with `status=429`; assert `PlaidTransientError`
  is raised.
- **Transient error — server error (HTTP 500)** — same pattern, `status=500`.
- **Permanent error — bad token (HTTP 400)** — `status=400`; assert
  `PlaidPermanentError` is raised.
- **Network error** — mock to raise `OSError`; assert `PlaidTransientError`
  is raised.

### Done when

- `PlaidClientAdapter.refresh_transactions(access_token)` is implemented.
- All five tests pass.
- Quality gate passes.

---

## Task 2: CLI `ledger refresh` command ✅ DONE

### What

Add a `refresh` command to `cli.py` with three modes mirroring `ledger sync`:
default (single-item via `PLAID_ACCESS_TOKEN`), `--item <id>`, and `--all`.

### Private helper functions

Follow the same decomposition as `ledger sync`. Add three private helpers:

#### `_refresh_default_mode() -> None`

```
1. Call load_config(require_plaid=True); on ConfigError print
   "refresh: <error>" and raise SystemExit(2).
2. If config.plaid_access_token is None: print
   "refresh: Missing required environment variable(s): PLAID_ACCESS_TOKEN"
   and raise SystemExit(2).
3. Build PlaidClientAdapter.from_config(config).
4. Call adapter.refresh_transactions(config.plaid_access_token).
5. On success: typer.echo("refresh: OK").
6. On (PlaidPermanentError, PlaidTransientError) as exc:
   typer.echo(f"refresh: ERROR {exc}") and raise SystemExit(1).
```

#### `_refresh_named_item(item_id: str) -> None`

```
1. Load items_config via load_items_config().
2. Find item_cfg where cfg.id == item_id; if not found:
   print "refresh: item '<item_id>' not found in items.toml"
   and raise SystemExit(2).
3. Load token via load_merged_env().get(item_cfg.access_token_env);
   if None: print "refresh: <access_token_env> is not set" and raise SystemExit(2).
4. Call _load_client_config_for_sync() to build config (reuse existing helper).
5. Build PlaidClientAdapter.from_config(config).
6. Call adapter.refresh_transactions(token).
7. On success: typer.echo(f"refresh[{item_cfg.id}]: OK").
8. On (PlaidPermanentError, PlaidTransientError) as exc:
   typer.echo(f"refresh[{item_cfg.id}]: ERROR {exc}") and raise SystemExit(1).
```

#### `_refresh_all_items() -> None`

```
1. Load items_config; if empty: print "refresh --all: no items found in items.toml"
   and raise SystemExit(2).
2. Call _load_client_config_for_sync() to build config.
3. Build PlaidClientAdapter.from_config(config).
4. Load load_merged_env() once; iterate items_config:
   a. If token is None: print error line, increment failure_count, continue.
   b. Call adapter.refresh_transactions(token).
   c. On success: typer.echo(f"refresh[{item_cfg.id}]: OK"), increment success_count.
   d. On (RuntimeError, OSError) as exc:
      typer.echo(f"refresh[{item_cfg.id}]: ERROR {exc}"), increment failure_count.
5. typer.echo(f"refresh --all: {success_count} items refreshed, {failure_count} failed").
6. If failure_count > 0: raise SystemExit(1).
```

Note: catch `RuntimeError` in the `--all` loop (not the specific subclasses)
to match the pattern in `_sync_all_items`, since `PlaidSyncError` inherits
from `RuntimeError`.

### Command entry point

```python
@app.command()
def refresh(
    item: Annotated[
        str | None,
        typer.Option(
            "--item", help="Refresh a single item from items.toml by ID."
        ),
    ] = None,
    all_items: Annotated[
        int,
        typer.Option(
            "--all", count=True, help="Refresh all items listed in items.toml."
        ),
    ] = 0,
) -> None:
    """Ask Plaid to re-check institutions and fire SYNC_UPDATES_AVAILABLE."""
    if item is not None and all_items > 0:
        typer.echo("refresh: --item and --all are mutually exclusive")
        raise SystemExit(2)

    if item is None and all_items == 0:
        _refresh_default_mode()
        return

    if item is not None:
        _refresh_named_item(item)
        return

    _refresh_all_items()
```

### Tests

Add to `tests/test_cli_sync.py` or a new `tests/test_cli_refresh.py` — choose
whichever keeps the file under the 2 000-line threshold.

Use the same mock/fixture pattern as the existing sync CLI tests: patch
`PlaidClientAdapter.refresh_transactions` to control success vs. error.

- **Default mode — success** — `PLAID_ACCESS_TOKEN` set; mock returns `None`;
  assert stdout contains `"refresh: OK"` and exit code is 0.
- **Default mode — missing token** — `PLAID_ACCESS_TOKEN` not set;
  assert stdout contains `"PLAID_ACCESS_TOKEN"` and exit code is 2.
- **Default mode — permanent error** — mock raises `PlaidPermanentError`;
  assert stdout contains `"refresh: ERROR"` and exit code is 1.
- **Default mode — transient error** — mock raises `PlaidTransientError`;
  assert stdout contains `"refresh: ERROR"` and exit code is 1.
- **`--item` — success** — item exists in items.toml, token set;
  assert stdout contains `"refresh[<item_id>]: OK"` and exit code is 0.
- **`--item` — item not found** — unknown item ID;
  assert stdout contains `"not found in items.toml"` and exit code is 2.
- **`--item` — missing token** — item found but env var not set;
  assert exit code is 2.
- **`--item` — adapter error** — mock raises `PlaidPermanentError`;
  assert stdout contains `"refresh[<item_id>]: ERROR"` and exit code is 1.
- **`--all` — all success** — two items, both succeed;
  assert stdout contains `"2 items refreshed, 0 failed"` and exit code is 0.
- **`--all` — partial failure** — two items, one succeeds, one raises
  `PlaidTransientError`; assert stdout contains `"1 items refreshed, 1 failed"`
  and exit code is 1.
- **`--all` — missing token for one item** — one item missing token; assert it
  is reported as a failure (exit code 1, failure count 1).
- **`--all` — no items in items.toml** — assert exit code is 2.
- **Mutual exclusion** — `--item foo --all` together; assert exit code is 2
  and stdout contains `"mutually exclusive"`.

### Done when

- `ledger refresh`, `ledger refresh --item <id>`, and `ledger refresh --all`
  are all functional.
- All tests listed above pass.
- Quality gate passes.

---

## Acceptance criteria for Sprint 24

- `ledger refresh` (no flags) calls `/transactions/refresh` for the singleton
  `PLAID_ACCESS_TOKEN` item and prints confirmation.
- `ledger refresh --item <id>` calls `/transactions/refresh` for the named item
  from `items.toml` and prints confirmation.
- `ledger refresh --all` calls `/transactions/refresh` for every item in
  `items.toml`, reporting per-item success/failure and a final summary.
- Missing or invalid access tokens follow the same exit-code conventions as
  `ledger sync` (missing config → exit 2, adapter error → exit 1).
- `--item` and `--all` are mutually exclusive; combined use exits 2.
- No API endpoint added, no schema changed, no skill bundles modified.
- Full quality gate passes with no regressions.
