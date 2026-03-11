# Sprint 9 — M8: Multi-item management

## Sprint goal

Enable the operator to onboard any household Plaid institution end-to-end from the
command line and to confirm the health of every configured item without running a
full sync. Sprint 9 is complete when `ledger link` captures a production access
token through a self-contained browser flow, `ledger items` gives an at-a-glance
health view of all configured items, and the household configuration is documented
with a concrete Alice/Bob/bank-alice/card-bob example.

## Working agreements

- Keep each task reviewable in one PR where possible.
- Preserve backward compatibility for all existing sync, doctor, and serve workflows.
- The Link server must bind to `127.0.0.1` only — never expose to the network.
- Run the quality gate before every commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Add or update tests for every behavior change.
- No new runtime dependencies without explicit justification; prefer stdlib for the
  local HTTP server.

## Task breakdown

---

### Task 1: `ledger link` — Local web-server Plaid Link flow

**Scope**

Implement a `ledger link` CLI command that guides the operator through the complete
Plaid Link flow in a browser and prints the resulting `access_token` and `item_id`
for storage in `items.toml`. This is a one-shot, interactive command that starts a
temporary local HTTP server, opens the browser, and exits after the exchange is
complete.

**User-facing flow**

```
$ ledger link
Creating Plaid link token...
Starting local Link server at http://127.0.0.1:18790
Opening browser — complete the Plaid Link flow to connect your institution.

[operator logs in to their bank in the browser window]

Link complete. Exchanging token...

  access_token : access-production-xxxxxxxxxxxxxxxxxxxxxxxx
  item_id      : XXXXXXXXXXXXXXXXXXXXXXXXXX

Add to items.toml and set the matching env var:

  [[items]]
  id                = "usaa-aron"
  access_token_env  = "PLAID_ACCESS_TOKEN_BANK_ALICE"
  owner             = "alice"

  export PLAID_ACCESS_TOKEN_BANK_ALICE="access-production-xxxx..."
```

**Implementation notes**

1. **New Plaid adapter methods** — add two methods to `PlaidClientAdapter` (or the
   underlying adapter interface) behind the existing adapter boundary:
   - `create_link_token(user_client_id: str, products: list[str],
     country_codes: list[str]) -> str` — calls Plaid `/link/token/create`, returns
     `link_token`.
   - `exchange_public_token(public_token: str) -> tuple[str, str]` — calls Plaid
     `/item/public_token/exchange`, returns `(access_token, item_id)`.

2. **Local HTTP server** — use Python's stdlib `http.server` (or a minimal
   `threading`-based wrapper) bound to `127.0.0.1` on a fixed port (`18790`; make
   it a module-level constant). The server handles two routes:
   - `GET /` — serves the Plaid Link HTML page with the `link_token` injected.
   - `POST /callback` — receives `{"public_token": "..."}` from the in-page JS;
     exchanges the token and stores the result; signals the main thread to shut down.

3. **HTML template** — an inline HTML string (no external template files needed)
   that:
   - Loads Plaid Link JS from `https://cdn.plaid.com/link/v2/stable/link-initialize.js`.
   - Initializes Plaid Link with the `link_token` and the `onSuccess` callback.
   - `onSuccess(public_token, metadata)` does `fetch('/callback', ...)` with the
     `public_token`.
   - Shows a success/error message in the browser so the operator knows they can
     close it.

4. **CLI command** — the `link` subcommand in `cli.py`:
   - Loads config (requires `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`; same
     as sync client config).
   - Accepts an optional `--products` flag (default `transactions`; can be passed
     multiple times).
   - Calls `create_link_token`, starts the local server in a background thread, opens
     the browser via `webbrowser.open`, then blocks on the main thread until the
     callback fires or the user hits Ctrl-C.
   - Prints the exchange result and the `items.toml` snippet on success.
   - Exits non-zero on error or Ctrl-C.

5. **No new runtime dependencies** — `http.server`, `threading`, `webbrowser`, and
   `json` are all stdlib. The Plaid Link JS is loaded from Plaid's CDN by the
   operator's browser; no server-side dependency is added.

**Done when**

- `ledger link` runs against Plaid sandbox and exchanges a `public_token` for a
  printed `access_token`.
- The local server binds to `127.0.0.1:18790` and shuts down cleanly after the
  exchange (or on Ctrl-C).
- `create_link_token` and `exchange_public_token` are tested with a mocked Plaid
  HTTP client (no real network calls in CI).
- CLI-level tests verify: config-error path (missing required env), link-token-error
  path, successful exchange path (mock server + mock Plaid calls), and Ctrl-C
  graceful exit.

---

### Task 2: `ledger items` command and household configuration finalization

**Scope**

Add a dedicated `ledger items` command for daily item health checks and relink
triage, and deliver a concrete household `items.toml.example` file with documentation
that establishes `sync --all` as the standard ingestion path.

**2a — `ledger items` command**

New CLI subcommand that reads `items.toml` and the SQLite DB and emits a one-line
status per item:

```
$ ledger items
items: usaa-aron      owner=aron      token=SET    accounts=3  last_synced=2026-03-10T14:22
items: amex-aron      owner=aron      token=SET    accounts=1  last_synced=2026-03-10T14:23
items: amex-michelle  owner=michelle  token=MISSING  accounts=0  last_synced=never
items: 2/3 items healthy, 1 need attention
```

Columns:
- `id` — from `items.toml`
- `owner` — from `items.toml` (`(none)` if null)
- `token` — `SET` if the `access_token_env` env var is non-empty, `MISSING` if not
- `accounts` — count of rows in `accounts` table with matching `item_id`
- `last_synced` — `last_synced_at` from `sync_state` for this item (`never` if no row)

Summary line: count of items with `token=SET` vs total configured items. An item
is "healthy" if `token=SET`. Items with `token=MISSING` are the relink candidates.

Behavior:
- If `items.toml` is absent or empty, print `items: no items configured — create items.toml` and exit 0.
- If `items.toml` has a parse error, print the error and exit 1.
- Exits 0 regardless of token-missing status (this is a display command, not a gating check).

**2b — `items.toml.example`**

Create `items.toml.example` at the repo root. This file is committed and shows a
representative two-person household structure:

```toml
# items.toml — Household Plaid item configuration
# Copy to ~/.config/claw-plaid-ledger/items.toml and populate env vars.
# Run: ledger sync --all

[[items]]
id                = "bank-alice"
access_token_env  = "PLAID_ACCESS_TOKEN_BANK_ALICE"
owner             = "alice"

[[items]]
id                = "card-alice"
access_token_env  = "PLAID_ACCESS_TOKEN_CARD_ALICE"
owner             = "alice"

[[items]]
id                = "card-bob"
access_token_env  = "PLAID_ACCESS_TOKEN_CARD_BOB"
owner             = "bob"
```

**2c — Documentation updates**

- `RUNBOOK.md`: add a short "Daily operations" section pointing to `ledger items`
  and `ledger sync --all`. Update any references to single-item sync to make clear
  that `sync --all` is the standard household path. Reference `items.toml.example`.
- `ARCHITECTURE.md`: update the `items.toml` example block and the multi-institution
  section if any placeholders need refreshing; keep alice/bob examples consistent.
- `README.md`: add `ledger items` to the command reference table/list if one exists.

**Done when**

- `ledger items` renders the per-item health table against a real or test DB.
- Items with missing token env vars are clearly flagged as `MISSING` (not a crash).
- `items.toml.example` is committed at repo root with a clear example structure.
- Documentation updates are complete and consistent with the example file.
- Tests cover: no-`items.toml` path, parse-error path, mixed SET/MISSING tokens,
  account and sync-state counts from a seeded test DB.

---

### Task 3: Sprint closeout and acceptance validation

**Scope**

Validate M8 acceptance at sprint end and mark completion in this file.

**Checklist**

- `ledger link` implemented, tested, and documented in RUNBOOK.md.
- `ledger items` implemented with tests; household example committed.
- `ARCHITECTURE.md` and `RUNBOOK.md` updated to reflect M8 state.
- All quality gates green.
- Update this file by appending `✅ DONE` to each completed task heading.
- Add final "Sprint 9 closeout ✅ DONE" section summarizing what shipped and any
  explicitly deferred follow-ups.

---

## Acceptance criteria for Sprint 9

- `ledger link` completes a Plaid Link flow from the command line and prints an
  `access_token` and `items.toml` snippet without requiring any external tooling.
- `ledger items` shows per-item health (token presence, account count, last sync)
  for all entries in `items.toml`.
- `items.toml.example` is committed with a real-household structure.
- `ledger sync --all` is established in docs as the primary household ingestion path.
- All existing workflows (`doctor`, `sync`, `serve`, `preflight`) are unbroken.
- Quality gate passes:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`

## Explicitly deferred (remain out of scope in Sprint 9)

- Canonical duplicate suppression across overlapping items (M9).
- Multi-item webhook routing (M10).
- Automated background Link re-auth / re-link detection.
- Parallel multi-institution sync.
