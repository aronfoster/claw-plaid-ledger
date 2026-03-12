# Sprint 11 — M10: Automation & Connectivity

## Sprint goal

Move from manually triggered sync patterns to webhook-first ingestion with
deterministic item routing and explicit fallback behavior. By the end of this
sprint Plaid webhooks route to the correct configured item in a multi-item
household; a configurable scheduled-sync fallback fires for items that have
gone silent; and the RUNBOOK covers DNS/DuckDNS setup so the operator can
maintain a stable, public webhook URL.

## Working agreements

- Keep each task reviewable in one PR where possible.
- Preserve backward compatibility for all existing sync, doctor, serve, and
  items workflows. In particular, users without `items.toml` must continue
  to work via the legacy `PLAID_ACCESS_TOKEN` env-var path.
- Raw ingestion must remain complete; no suppressions or deletions in the sync
  engine.
- Run the quality gate before every commit:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Add or update tests for every behavior change.
- No new runtime dependencies without explicit justification.

## Runtime behavior contract (codified in this sprint)

| Trigger | Behavior |
|---|---|
| Plaid webhook (`SYNC_UPDATES_AVAILABLE`) | **Primary.** Route to the matching configured item and sync it immediately. |
| Scheduled sync fallback | **Secondary.** Fires only when an item has not been synced in the configured fallback window (default 24 h). Opt-in via `CLAW_SCHEDULED_SYNC_ENABLED=true`. |
| `ledger sync [--all / --item]` | Manual / operator-initiated. Unchanged from M9. |
| OpenClaw poke | Fires after every sync that produces transaction changes (added + modified + removed > 0). One poke per sync run, regardless of which item triggered it. |

## Task breakdown

---

### Task 1: Multi-item webhook routing ✅ DONE

**Scope**

The current webhook handler ignores `item_id` in the Plaid payload and always
syncs the `PLAID_ACCESS_TOKEN` singleton. Teach it to extract `item_id` from
the payload, find the matching `ItemConfig` in `items.toml`, and pass the
correct access token to `_background_sync()`. Fall back to the single-item
env-var path when no items.toml exists or the payload item_id is not
configured.

**Implementation notes**

1. **Extract `item_id` from webhook payload** — Plaid includes `item_id` as a
   top-level string field on every webhook. The handler already receives the
   parsed body as a dict; read `body.get("item_id")`.

2. **Refactor `_background_sync()`** — give it optional parameters so the
   caller can inject item-specific context:

   ```python
   async def _background_sync(
       *,
       access_token: str | None = None,
       item_id: str | None = None,
       owner: str | None = None,
   ) -> None:
   ```

   When `access_token` is `None`, the function falls back to loading
   `PLAID_ACCESS_TOKEN` from config (existing behavior).

3. **Item lookup in the webhook handler** — after verifying the signature and
   confirming `SYNC_UPDATES_AVAILABLE`:

   ```
   payload_item_id = body.get("item_id")

   if payload_item_id and items.toml is loadable:
       find ItemConfig where id == payload_item_id
       if found:
           resolve access_token from ItemConfig.access_token_env
           enqueue _background_sync(access_token=token, item_id=cfg.id, owner=cfg.owner)
       else:
           log warning "item_id {x} not found in items.toml; falling back to PLAID_ACCESS_TOKEN"
           enqueue _background_sync()   # legacy single-item fallback
   else:
       enqueue _background_sync()       # no item_id or no items.toml
   ```

4. **Fallback priority rule** — the fallback must not silently swallow the case
   where `items.toml` exists but the webhook item_id is missing from it. Log a
   `WARNING` to make this visible to the operator.

5. **Notification** — `notify_openclaw()` is already called inside
   `_background_sync()` when changes are non-zero; no change needed. The log
   message may optionally include the item id for traceability.

**Done when**

- A `SYNC_UPDATES_AVAILABLE` webhook whose `item_id` matches a configured item
  triggers sync for that specific item's access token.
- A webhook with an `item_id` absent from `items.toml` logs a warning and falls
  back to the `PLAID_ACCESS_TOKEN` single-item sync.
- A webhook with no `item_id` field falls back to the single-item sync (no
  warning needed — Plaid always sends it, but defensive handling is required).
- Users with no `items.toml` continue to work exactly as before.
- `_background_sync()` signature is backward-compatible: calling it with no
  arguments still works.
- Tests cover: item found and routed correctly; item_id not in config (warning
  + fallback); no items.toml (fallback); no item_id in payload (fallback);
  env-var resolution failure raises and is caught; notification fires when
  changes > 0.
- All quality gates pass.

---

### Task 2: Scheduled sync fallback ✅ DONE

**Scope**

Add an opt-in background scheduler that fires a sync for any configured item
that has not been synced within a configurable window (default 24 hours). This
is the safety net for missed or failed webhooks. Implementation must use
`asyncio` only — no new scheduler library dependency.

**New configuration**

Add to `config.py` and `.env` loading:

| Env var | Type | Default | Description |
|---|---|---|---|
| `CLAW_SCHEDULED_SYNC_ENABLED` | bool | `false` | Enable the scheduled sync fallback loop. |
| `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` | int | `24` | Hours of sync silence before an item is considered overdue. |

Parse `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` with a minimum value of 1; reject
values ≤ 0 with a startup error.

**Implementation notes**

1. **FastAPI lifespan context manager** — add a `lifespan` function to
   `server.py` using `@asynccontextmanager`. On startup, if
   `CLAW_SCHEDULED_SYNC_ENABLED=true`, create an asyncio background task
   running `_scheduled_sync_loop()`. Cancel it cleanly on shutdown:

   ```python
   @asynccontextmanager
   async def lifespan(app: fastapi.FastAPI):
       task = None
       if config.scheduled_sync_enabled:
           task = asyncio.create_task(_scheduled_sync_loop(config))
       yield
       if task:
           task.cancel()
           with contextlib.suppress(asyncio.CancelledError):
               await task

   app = fastapi.FastAPI(title="claw-plaid-ledger", lifespan=lifespan)
   ```

2. **`_scheduled_sync_loop()`** — runs forever, waking every 60 minutes to
   check for overdue items:

   ```python
   async def _scheduled_sync_loop(config) -> None:
       while True:
           await asyncio.sleep(3600)   # check once per hour
           await _check_and_sync_overdue_items(config)
   ```

   The 60-minute poll interval is not configurable (it is not the fallback
   window; it is just the check frequency). Document this in a comment.

3. **`_check_and_sync_overdue_items()`** — for each item in `items.toml` (or
   the single-item env-var fallback if no `items.toml`):

   - Read `sync_state.last_synced` from the DB for that item's `item_id`.
   - If `last_synced` is `None` or older than `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS`
     hours ago → call `_background_sync()` with the item's credentials.
   - Log an INFO message: `"scheduled-sync: item {id} overdue ({n}h since last
     sync); triggering fallback sync"`.
   - Skip items where `last_synced` is recent; log DEBUG for each skipped item.
   - Catch all exceptions per item so one failure does not prevent others from
     being checked.

4. **`doctor` check** — add a new entry to the `doctor` output that reports
   scheduled sync configuration:

   ```
   scheduled-sync: DISABLED (set CLAW_SCHEDULED_SYNC_ENABLED=true to enable)
   ```
   or
   ```
   scheduled-sync: ENABLED — fallback window 24h, check interval 60min
   ```

   This check is informational only; it does not cause `doctor` to exit
   non-zero.

**Done when**

- `CLAW_SCHEDULED_SYNC_ENABLED=false` (default): no background task is started;
  no behavior change from M9.
- `CLAW_SCHEDULED_SYNC_ENABLED=true`: a background loop starts at server
  startup and is cancelled cleanly on shutdown.
- Items overdue by more than `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` hours trigger
  a sync call; recently synced items are skipped.
- A single item's sync failure does not prevent others from being checked in
  the same pass.
- `doctor` reports scheduled sync state correctly.
- `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS=0` or negative is rejected with a clear
  startup error.
- Tests cover: disabled (no task created), enabled with overdue item (sync
  triggered), enabled with recent item (sync skipped), item with no sync state
  (treated as overdue), one item fails (others still checked), `doctor` output
  for enabled and disabled states, invalid fallback hours rejected.
- All quality gates pass.

---

### Task 3: DuckDNS webhook URL setup guidance

**Scope**

Operators running `ledger serve` on a home server need a stable, public-facing
URL for Plaid to deliver webhooks. Add practical DuckDNS setup instructions to
`RUNBOOK.md` and commit a reusable IP-update shell script to the repo.

**Deliverables**

1. **New RUNBOOK.md section: "Stable webhook URL with DuckDNS"** — add after
   the existing "Plaid webhook setup" content. Cover:

   - Why a stable public URL is needed (Plaid requires a pre-registered
     webhook URL; home IPs change).
   - Account and subdomain registration at duckdns.org.
   - How to find your DuckDNS token.
   - Pointing Plaid to `https://<subdomain>.duckdns.org/webhooks/plaid`.
   - Router/firewall port-forward requirements (external 443 → internal
     `CLAW_SERVER_PORT`).
   - TLS termination note: recommend a reverse proxy (nginx, Caddy) to handle
     TLS; `ledger serve` listens on plain HTTP internally.
   - Testing the webhook URL with `curl` before registering with Plaid.

2. **`scripts/duckdns-update.sh`** — a minimal POSIX shell script that updates
   the DuckDNS IP record:

   ```sh
   #!/bin/sh
   # Usage: DUCKDNS_TOKEN=<token> DUCKDNS_DOMAIN=<subdomain> ./duckdns-update.sh
   # Suitable for cron or a systemd timer. Logs result to stdout.
   set -eu
   DUCKDNS_TOKEN="${DUCKDNS_TOKEN:?DUCKDNS_TOKEN is required}"
   DUCKDNS_DOMAIN="${DUCKDNS_DOMAIN:?DUCKDNS_DOMAIN is required}"
   RESULT=$(curl -fsSL \
     "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=")
   echo "duckdns-update: ${DUCKDNS_DOMAIN} → ${RESULT}"
   ```

   The script must use env vars only; no hardcoded credentials. Add a cron
   example comment: `*/5 * * * * DUCKDNS_TOKEN=... DUCKDNS_DOMAIN=... /path/to/duckdns-update.sh`.

3. **RUNBOOK.md section: "Scheduled sync fallback"** — a short operational
   note explaining when and why to enable `CLAW_SCHEDULED_SYNC_ENABLED`,
   cross-referencing Task 2. This is a pure docs addition; keep it brief
   (≤ 10 lines).

**Done when**

- `RUNBOOK.md` has a clear DuckDNS setup walkthrough that a new operator can
  follow end-to-end.
- `scripts/duckdns-update.sh` is committed, executable (`chmod +x`), and
  validated with `shellcheck` (if available in the environment).
- `RUNBOOK.md` has a scheduled sync fallback operations note.
- No new Python code or dependencies introduced.
- All quality gates pass (docs only; ruff/mypy/pytest are unaffected).

---

### Task 4: Sprint closeout, docs, and acceptance validation

**Scope**

Update project documentation to reflect the M10 implementation and validate all
acceptance criteria.

**Checklist**

- `ARCHITECTURE.md`:
  - Update webhook flow description to reflect item-routing logic.
  - Add scheduled sync fallback to the runtime behavior section.
  - Add `CLAW_SCHEDULED_SYNC_ENABLED` and `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS`
    to the configuration reference table.
  - Update CLI/server module descriptions if new helpers were added.
- `RUNBOOK.md`:
  - Add `ledger serve` startup checklist entry: confirm
    `CLAW_SCHEDULED_SYNC_ENABLED` intent before launch.
  - Update webhook setup section to cross-reference the DuckDNS guidance from
    Task 3.
- `ROADMAP.md`:
  - Move M10 from "Upcoming Milestones" to "Completed Milestones".
- `SPRINT.md`:
  - Append `✅ DONE` to each completed task heading.
  - Add "Sprint 11 closeout ✅ DONE" section summarising what shipped and any
    explicitly deferred follow-ups.
- Quality gate must pass at closeout:
  - `uv run --locked ruff format . --check` ✅
  - `uv run --locked ruff check .` ✅
  - `uv run --locked mypy .` ✅
  - `uv run --locked pytest -v` ✅

---

## Acceptance criteria for Sprint 11

- A `SYNC_UPDATES_AVAILABLE` webhook whose `item_id` matches a configured item
  syncs that item's access token, not the `PLAID_ACCESS_TOKEN` singleton.
- An unrecognised `item_id` in a webhook falls back to the single-item env-var
  path with a logged warning; no crash, no silent drop.
- Users with no `items.toml` see identical behavior to M9 (full backward
  compat).
- `CLAW_SCHEDULED_SYNC_ENABLED=false` (default): server starts with no
  background loop and no behavior change.
- `CLAW_SCHEDULED_SYNC_ENABLED=true`: overdue items are synced automatically;
  recently-synced items are skipped; the loop shuts down cleanly.
- `doctor` reports scheduled sync configuration state.
- `RUNBOOK.md` has actionable DuckDNS setup instructions and a
  `scripts/duckdns-update.sh` script.
- All existing workflows (`doctor`, `sync`, `serve`, `items`, `link`,
  `preflight`, `apply-precedence`, `overlaps`) are unbroken.
- Quality gate passes.

## Explicitly deferred (remain out of scope in Sprint 11)

- Parallel multi-institution sync (sequential is sufficient for household scale).
- Automatic `apply-precedence` on every sync.
- Transfer detection and internal movement suppression (M12).
- Per-agent token scoping.
- Richer operator review queue UX.
