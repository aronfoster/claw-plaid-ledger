# Sprint 4 — M3: Server skeleton and webhook receiver

## Sprint goal

Stand up a FastAPI server that Plaid can reach, receives
`SYNC_UPDATES_AVAILABLE` webhooks, triggers the existing sync engine
asynchronously, and is secured with bearer token auth and Plaid HMAC
verification from day one.

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

## Task breakdown

### Task 1: FastAPI dependency and `ledger serve` skeleton ✅ DONE

**Scope**

- Add `fastapi` and `uvicorn` to `pyproject.toml` dependencies
- Add a `server.py` module that creates the FastAPI application instance
- Add `ledger serve` CLI command that starts uvicorn on host/port
  configurable via `CLAW_SERVER_HOST` (default `127.0.0.1`) and
  `CLAW_SERVER_PORT` (default `8000`) — local-only by default
- Add `GET /health` returning `{"status": "ok"}`; no auth required
- Document the two new env vars in `.env.example` and `ARCHITECTURE.md`

**Done when**

- `ledger serve` starts without error and `GET /health` returns 200
- Server binds to `127.0.0.1` by default (never `0.0.0.0` unless
  explicitly configured)

**Testing expectation**

- Test that the FastAPI app is importable and `/health` returns 200 via
  the FastAPI `TestClient`; no live uvicorn process required in tests

---

### Task 2: Bearer token auth and startup validation ✅ DONE

**Scope**

- Add `CLAW_API_SECRET` to `Config` and `.env.example`
- `ledger serve` must refuse to start if `CLAW_API_SECRET` is not set,
  printing a clear error message
- Add a FastAPI dependency that enforces `Authorization: Bearer <token>`
  on all routes except `/health`; return 401 on missing or invalid token
- Extend `ledger doctor` to check that `CLAW_API_SECRET` is set and
  report `[OK]` or `[FAIL]` accordingly
- Document `CLAW_API_SECRET` in `ARCHITECTURE.md` configuration table

**Done when**

- Server refuses to start without `CLAW_API_SECRET`
- Requests to protected routes without a valid token return 401
- `doctor` reports the secret's presence (not its value)

**Testing expectation**

- Test: missing token returns 401
- Test: wrong token returns 401
- Test: correct token passes through to the route
- Test: `doctor` output reflects set/unset state of `CLAW_API_SECRET`

---

### Task 3: Plaid webhook signature verification ✅ DONE

**Scope**

- Implement a standalone `verify_plaid_signature(body: bytes, headers:
  dict) -> bool` function in a new `webhook_auth.py` module
- Verification must follow Plaid's documented HMAC-SHA256 scheme using
  `PLAID_WEBHOOK_SECRET` (add to `Config` and `.env.example`)
- The function should be pure and testable without a live server
- If `PLAID_WEBHOOK_SECRET` is not set, verification fails closed
  (returns `False`), never open

**Done when**

- Valid signatures return `True`; tampered body or wrong secret return
  `False`
- Unset secret returns `False` without raising

**Testing expectation**

- Test: valid signature passes
- Test: wrong secret fails
- Test: tampered body fails
- Test: missing secret fails closed

---

### Task 4: `POST /webhooks/plaid` handler ✅ DONE

**Scope**

- Add `POST /webhooks/plaid` endpoint; requires bearer token
- Verify Plaid HMAC signature using the function from Task 3; return 400
  on failure
- On `SYNC_UPDATES_AVAILABLE` webhook type, enqueue a background sync
  using FastAPI's `BackgroundTasks`; return 200 immediately (Plaid's
  10-second timeout must not be breached)
- The background task calls the existing `run_sync` via a thin
  `PlaidClientAdapter` constructed from config; errors are logged but do
  not affect the 200 response already sent
- Unrecognised webhook types are acknowledged with 200 and logged at
  debug level; do not error on unknown types (Plaid may send others)
- Document the endpoint in `ARCHITECTURE.md`

**Done when**

- `SYNC_UPDATES_AVAILABLE` webhook triggers a background sync
- Invalid signature returns 400
- Unknown webhook types return 200 without error

**Testing expectation**

- Test: valid `SYNC_UPDATES_AVAILABLE` payload enqueues sync and returns
  200; verify the background task is invoked (mock `run_sync`)
- Test: invalid signature returns 400
- Test: unknown webhook type returns 200
- Test: sync errors in background do not bubble up to the HTTP response

---

### Task 5: Structured logging ✅ DONE

**Scope**

- Configure Python's standard `logging` module at server startup with a
  format suitable for systemd/journald: no ANSI color, timestamps, level,
  module name, and message; e.g.
  `%(asctime)s %(levelname)s %(name)s: %(message)s`
- Log level configurable via `CLAW_LOG_LEVEL` env var (default `INFO`);
  add to `Config`, `.env.example`, and `ARCHITECTURE.md`
- Establish consistent log coverage across the new server surface:
  - `INFO` — server started (host, port, log level; never the secret
    value); webhook received and acknowledged; sync triggered; sync
    completed (accounts/added/modified/removed counts)
  - `WARNING` — unknown webhook type received; sync completed with zero
    results when changes were expected (has_more drained but counts all
    zero)
  - `ERROR` — signature verification failed; background sync raised an
    exception (log the exception with traceback)
- Existing CLI commands (`doctor`, `sync`, `init-db`) are unaffected;
  they continue to use `typer.echo` for operator output

**Done when**

- `journalctl -u claw-plaid-ledger` shows structured, human-readable
  entries covering the above events
- Log level can be changed without a code edit
- No secrets or access tokens appear in any log line

**Testing expectation**

- Test: server startup emits an `INFO` line containing host and port
- Test: `ERROR` is logged when signature verification fails
- Test: `ERROR` with traceback is logged when background sync raises
- Test: `CLAW_LOG_LEVEL=DEBUG` is accepted without error; `CLAW_LOG_LEVEL=INVALID` raises a clear `ConfigError` at startup

---

## Acceptance criteria for the sprint

- `ledger serve` starts, binds locally, and serves `/health`
- All non-health endpoints require a valid bearer token
- Plaid webhook signature is verified before any sync is triggered
- `SYNC_UPDATES_AVAILABLE` triggers a background sync without blocking
  the HTTP response
- `ledger doctor` reports `CLAW_API_SECRET` presence
- Quality gate passes on all PRs
- `ARCHITECTURE.md` reflects all new config vars and endpoints

## Explicitly deferred

- Agent query API and annotations (M4)
- OpenClaw notification (M5)
- Any markdown export
- Multi-institution webhook routing
