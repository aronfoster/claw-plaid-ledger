# Sprint 6 — M5: OpenClaw notification

## Sprint goal

After a webhook-triggered sync, wake Hestia when there are transactions worth
reviewing. A successful sync that adds, modifies, or removes at least one
transaction sends a `POST` to OpenClaw's local `/hooks/agent` endpoint. Zero-change
syncs remain silent. The operator can opt out by leaving `OPENCLAW_HOOKS_TOKEN`
unset — a warning is logged but nothing crashes.

## Working agreements

- Keep changes small and independently reviewable.
- Prefer one standalone task per PR unless a dependency forces a pair.
- Preserve strict quality gates on every PR:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest`
- Use standard-library `sqlite3` for the database layer (no change from M4).
- Use standard-library `urllib.request` for the notification HTTP call — do not
  add `httpx` to the runtime dependencies; it remains a dev/test-only dependency.
- Add appropriate unit and integration tests for each task.

## Conventions and implementation notes

**HTTP client for notification:** use `urllib.request.urlopen` with a
`urllib.request.Request` object. This is the only outbound HTTP call in the
notification path; keeping it in stdlib avoids promoting `httpx` from a test
utility to a runtime dependency.

**Zero-change gate:** the gate lives in `_background_sync` in `server.py`,
immediately after `run_sync` returns. Check
`summary.added + summary.modified + summary.removed > 0` before calling the
notifier. The existing `logger.warning("sync returned no changes")` log line
is already in place as the fallthrough.

**Graceful degradation:** if `OPENCLAW_HOOKS_TOKEN` is not set, the notifier
logs a `WARNING` and returns immediately — it does not raise. If the HTTP POST
itself fails (network error, non-2xx response), the notifier logs the error at
`WARNING` and returns — it must never propagate an exception back to
`_background_sync`.

**Message format:** build the message from the non-zero counts only, then
append the review prompt. Examples:

- `"Plaid sync complete: 3 added, 1 modified. Review new transactions and annotate as appropriate."`
- `"Plaid sync complete: 5 added. Review new transactions and annotate as appropriate."`
- `"Plaid sync complete: 2 removed. Review new transactions and annotate as appropriate."`

Produce the count fragment by joining non-zero entries from
`[f"{n} added", f"{n} modified", f"{n} removed"]` with `", "`. This keeps the
message clean and matches the example in the roadmap.

**Payload shape** (matches OpenClaw `/hooks/agent` spec):

```json
{
  "message": "Plaid sync complete: 3 added, 1 modified. Review new transactions and annotate as appropriate.",
  "name": "Hestia",
  "wakeMode": "now"
}
```

**Request headers:**

```
Content-Type: application/json
Authorization: Bearer <OPENCLAW_HOOKS_TOKEN>
```

---

## Task breakdown

### Task 1: Config additions for OpenClaw notification ✅ DONE

**Scope**

Add four new environment variables to `Config` in `config.py` and document
them in `.env.example`. Nothing else changes in this task.

**`config.py` additions — new fields on `Config`:**

```python
openclaw_hooks_url: str = "http://127.0.0.1:18789/hooks/agent"
openclaw_hooks_token: str | None = None
openclaw_hooks_agent: str = "Hestia"
openclaw_hooks_wake_mode: str = "now"
```

Reading rules (add to `load_config`, alongside the existing env-var reads):

| Field | Env var | Required | Default |
|---|---|---|---|
| `openclaw_hooks_url` | `OPENCLAW_HOOKS_URL` | no | `http://127.0.0.1:18789/hooks/agent` |
| `openclaw_hooks_token` | `OPENCLAW_HOOKS_TOKEN` | no | `None` |
| `openclaw_hooks_agent` | `OPENCLAW_HOOKS_AGENT` | no | `Hestia` |
| `openclaw_hooks_wake_mode` | `OPENCLAW_HOOKS_WAKE_MODE` | no | `now` |

None of these variables are required at startup — the server must start
successfully even when all four are absent.

**`.env.example` additions** — add a new section after the existing
`CLAW_LOG_LEVEL` block:

```
# OpenClaw notification (optional)
# After a sync that adds, modifies, or removes transactions, a POST is sent to
# OpenClaw's /hooks/agent endpoint to wake the configured agent. Set
# OPENCLAW_HOOKS_TOKEN to enable; leave unset to disable silently.
OPENCLAW_HOOKS_URL=http://127.0.0.1:18789/hooks/agent
OPENCLAW_HOOKS_TOKEN=
OPENCLAW_HOOKS_AGENT=Hestia
OPENCLAW_HOOKS_WAKE_MODE=now
```

**Done when**

- `load_config()` reads all four vars from the environment; defaults apply
  when vars are absent
- `Config` is a frozen dataclass as before; mypy passes with `--strict`
- `load_config()` called with none of the four vars set returns a valid
  `Config` with defaults applied

**Testing expectations**

- Test: all four vars absent → defaults applied (`url` matches the default,
  `token` is `None`, `agent` is `"Hestia"`, `wake_mode` is `"now"`)
- Test: all four vars set → values are read correctly
- Test: `OPENCLAW_HOOKS_TOKEN` set to empty string → stored as `None`
  (treat empty string as unset, consistent with how other optional secrets
  are handled in the existing config loader)

---

### Task 2: `notifier.py` module ✅ DONE

**Scope**

Create `src/claw_plaid_ledger/notifier.py` with a single public function
`notify_openclaw`. This module is the only place that constructs or sends the
OpenClaw notification payload.

**Function signature:**

```python
def notify_openclaw(
    *,
    added: int,
    modified: int,
    removed: int,
    url: str,
    token: str | None,
    agent: str,
    wake_mode: str,
) -> None:
```

**Implementation requirements:**

1. **Token guard:** if `token` is `None` or empty, log at `WARNING`:
   `"OPENCLAW_HOOKS_TOKEN not set — skipping notification"` and return.

2. **Message construction:** join the non-zero count fragments with `", "`
   and append the review prompt. See the message format examples in the
   sprint conventions above.

3. **Payload construction:**
   ```python
   payload = {
       "message": message,
       "name": agent,
       "wakeMode": wake_mode,
   }
   ```

4. **HTTP POST** using `urllib.request`:
   ```python
   import json
   import urllib.request

   data = json.dumps(payload).encode()
   req = urllib.request.Request(
       url,
       data=data,
       headers={
           "Content-Type": "application/json",
           "Authorization": f"Bearer {token}",
       },
       method="POST",
   )
   with urllib.request.urlopen(req, timeout=10) as resp:
       status = resp.status
   ```

5. **Error handling:**
   - If `urllib.error.URLError` (network failure, DNS, refused connection) is
     raised, log at `WARNING`:
     `f"OpenClaw notification failed (network): {e}"` and return.
   - If `urllib.error.HTTPError` (non-2xx response) is raised, log at
     `WARNING`:
     `f"OpenClaw notification failed: HTTP {e.code}"` and return.
   - On success, log at `INFO`:
     `f"OpenClaw notification sent: {status}"`.
   - **Never propagate any exception** from this function.

6. **Imports:** use only standard-library modules (`json`, `logging`,
   `urllib.request`, `urllib.error`). Do not import `httpx`.

**Done when**

- `notify_openclaw` with `token=None` logs a warning and makes no HTTP call
- `notify_openclaw` with a valid token constructs the correct payload and
  makes one POST to `url`
- A network error is caught, logged, and does not propagate
- A non-2xx HTTP response is caught, logged, and does not propagate
- `mypy --strict` passes on the module

**Testing expectations**

Use `unittest.mock.patch("urllib.request.urlopen")` to avoid real network
calls.

- Test: `token=None` → warning logged, `urlopen` never called
- Test: `token=""` (empty string) → same as `None`; warning logged,
  `urlopen` never called
- Test: `added=3, modified=1, removed=0` → message is
  `"Plaid sync complete: 3 added, 1 modified. Review new transactions and annotate as appropriate."`
- Test: `added=5, modified=0, removed=0` → message is
  `"Plaid sync complete: 5 added. Review new transactions and annotate as appropriate."`
- Test: `added=0, modified=0, removed=2` → message is
  `"Plaid sync complete: 2 removed. Review new transactions and annotate as appropriate."`
- Test: successful POST → `urlopen` called once with `method="POST"`,
  `Content-Type: application/json` header, `Authorization: Bearer <token>`
  header, and correct JSON body
- Test: `URLError` raised by `urlopen` → warning logged, no exception
  propagated
- Test: `HTTPError` with code 401 raised by `urlopen` → warning logged
  (`"OpenClaw notification failed: HTTP 401"`), no exception propagated

---

### Task 3: Wire notification into background sync ✅ DONE

**Scope**

Integrate `notify_openclaw` into `_background_sync` in `server.py`. This is
the only file that changes in this task.

**Location:** `_background_sync()` in `server.py` — the `except Exception`
handler that logs sync summary is already present. The notification call goes
immediately after the summary log, inside the same `try` block (before the
`except`).

**Change to `_background_sync`:**

After logging the sync summary, add:

```python
if summary.added + summary.modified + summary.removed > 0:
    from claw_plaid_ledger.notifier import notify_openclaw
    notify_openclaw(
        added=summary.added,
        modified=summary.modified,
        removed=summary.removed,
        url=cfg.openclaw_hooks_url,
        token=cfg.openclaw_hooks_token,
        agent=cfg.openclaw_hooks_agent,
        wake_mode=cfg.openclaw_hooks_wake_mode,
    )
```

The import may be placed at the top of `server.py` with the other imports
rather than inline — either style is acceptable; follow the existing pattern
in the file.

`cfg` is the `Config` object already loaded by `_background_sync`. No new
config loading is needed.

**Zero-change path:** when `summary.added + summary.modified + summary.removed
== 0`, the existing `logger.warning("sync returned no changes")` line (or
equivalent) fires and `notify_openclaw` is not called. Do not add any new
logging for this path.

**Done when**

- A background sync with `added=3, modified=1` calls `notify_openclaw` with
  the correct args
- A background sync with `added=0, modified=0, removed=0` does not call
  `notify_openclaw`
- A notification failure (simulated) does not surface as an unhandled
  exception in `_background_sync`
- `mypy --strict` and all four quality gates pass

**Testing expectations**

All tests in `test_server.py` (or a new `test_notification_wiring.py`).
Use `unittest.mock.patch` to mock `notify_openclaw`.

- Test: webhook `SYNC_UPDATES_AVAILABLE` → sync returns summary with changes
  → `notify_openclaw` called once with correct `added`, `modified`, `removed`
  args and config values
- Test: webhook `SYNC_UPDATES_AVAILABLE` → sync returns summary with
  `added=0, modified=0, removed=0` → `notify_openclaw` not called
- Test: `notify_openclaw` raises an unexpected exception (simulate a bug in
  the notifier) → `_background_sync` logs the error and does not re-raise
  (verify the background task completes without crashing the process)

---

### Task 4: `doctor` extension for notification config

**Scope**

Extend the `doctor` CLI command in `cli.py` to report whether OpenClaw
notification is configured. One new check; no other changes.

**New check — add after the existing `CLAW_API_SECRET` check:**

```
doctor: openclaw notification [OK] url=http://127.0.0.1:18789/hooks/agent agent=Hestia
```

or, when `OPENCLAW_HOOKS_TOKEN` is not set:

```
doctor: openclaw notification [WARN] OPENCLAW_HOOKS_TOKEN not set — notifications disabled
```

Implementation details:

- If `config.openclaw_hooks_token` is `None`: print the `[WARN]` line. Do
  **not** call `sys.exit(1)` — a missing token is a valid operator choice,
  not a configuration error.
- If `config.openclaw_hooks_token` is set: print the `[OK]` line showing the
  effective `url` and `agent` name. Do not print the token value.
- The `[WARN]` case must not cause `doctor` to exit with a non-zero status
  code by itself. Only hard failures (missing DB, missing schema, etc.) exit
  non-zero.

**Done when**

- `doctor` with `OPENCLAW_HOOKS_TOKEN` unset prints the `[WARN]` line and
  exits 0
- `doctor` with `OPENCLAW_HOOKS_TOKEN` set prints the `[OK]` line showing
  `url` and `agent`; token value is not printed
- Existing doctor checks are unaffected

**Testing expectations**

- Test: `OPENCLAW_HOOKS_TOKEN` not set → output contains
  `"openclaw notification [WARN]"` and `exit code == 0`
- Test: `OPENCLAW_HOOKS_TOKEN` set → output contains
  `"openclaw notification [OK]"` and includes the default URL and agent name;
  exit code == 0
- Test: `OPENCLAW_HOOKS_AGENT=Hal9000` set → output shows `agent=Hal9000`

---

### Task 5: `ARCHITECTURE.md` update

**Scope**

Update `ARCHITECTURE.md` to document the M5 integration pattern. No code
changes; documentation only.

**Sections to add or update:**

1. **Current milestone focus** — update from M4 to M5; note Sprint 6 added
   OpenClaw notification after webhook-triggered syncs.

2. **Components** — add `notifier.py` to the component list:
   `OpenClaw notifier (notifier.py)` — sends `POST /hooks/agent` to wake
   Hestia after a non-empty sync.

3. **Data flow** — extend to show the notification path:
   ```
   Plaid API -> sync engine -> SQLite -> Agent API -> OpenClaw agent
                     |
                     +--[non-empty sync]--> OpenClaw /hooks/agent (Hestia wake)
   ```

4. **New section: OpenClaw notification** — add after the existing
   "OpenAPI / SKILL definition" section. Cover:
   - When notification fires: after a webhook-triggered sync where
     `added + modified + removed > 0`
   - When notification is skipped: zero-change syncs; `OPENCLAW_HOOKS_TOKEN`
     not set (logged as warning, not an error)
   - Failure behaviour: network errors and non-2xx responses are logged at
     `WARNING` and never crash the background task
   - Payload shape with annotated fields (message, name, wakeMode)
   - The notifier uses `urllib.request` (stdlib); no new runtime dependency

5. **Configuration table** — add the four new variables:

   | Variable | Required | Default | Description |
   |---|---|---|---|
   | `OPENCLAW_HOOKS_URL` | no | `http://127.0.0.1:18789/hooks/agent` | OpenClaw `/hooks/agent` endpoint URL |
   | `OPENCLAW_HOOKS_TOKEN` | no | — | Bearer token for OpenClaw; if unset, notification is skipped with a warning |
   | `OPENCLAW_HOOKS_AGENT` | no | `Hestia` | Name of the OpenClaw agent to wake |
   | `OPENCLAW_HOOKS_WAKE_MODE` | no | `now` | Wake mode passed to OpenClaw (`now` is the only supported value) |

6. **Repository layout** — add `notifier.py` to the `src/claw_plaid_ledger/`
   listing.

**Done when**

- A developer can understand the full notification flow from `ARCHITECTURE.md`
  alone without reading `server.py` or `notifier.py`
- All four new config variables are documented with their defaults and
  semantics
- The "skip on missing token" and "fail gracefully" behaviours are explicitly
  documented

---

## Acceptance criteria for the sprint

- After a webhook-triggered sync with at least one added, modified, or removed
  transaction, a `POST` is sent to `OPENCLAW_HOOKS_URL` with the correct JSON
  payload and `Authorization: Bearer <OPENCLAW_HOOKS_TOKEN>` header
- Zero-change syncs do not trigger a notification
- When `OPENCLAW_HOOKS_TOKEN` is not set, the server logs a warning and
  continues normally — no crash, no change to sync behaviour
- Network failures and non-2xx responses from the OpenClaw endpoint are logged
  at `WARNING` and do not interrupt the background sync task
- `ledger doctor` reports `[OK]` when the token is set, and `[WARN]
  notifications disabled` when it is not; either way `doctor` exits 0
- All four quality gates pass on every PR:
  `ruff format`, `ruff check`, `mypy --strict`, `pytest`
- `ARCHITECTURE.md` documents the notification flow, new config variables, and
  graceful-degradation behaviour

## Explicitly deferred

- Retry logic for failed notification attempts
- Per-institution notification routing (M6 and beyond)
- Notification on `ledger sync` (CLI path) — only the webhook-triggered
  background sync notifies in this milestone
- Configurable notification templates or per-agent message customisation
