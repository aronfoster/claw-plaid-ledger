# Sprint 28 — Scheduled Sync (Webhook Retirement) (M25)

## Sprint goal

Replace inbound webhook infrastructure with a reliable outbound pull cadence.
Promote `ledger sync --all` via systemd timer as the primary sync mechanism.
Gate webhook code behind an opt-in flag. Add CLI-driven post-sync agent
notification so the timer can wake Hestia after new transactions arrive.

## Background

The Plaid webhook path (`POST /webhooks/plaid`) has never worked reliably in
multi-item setups (BUG-018). The item-ID lookup compares Plaid-assigned item
IDs against operator-assigned logical IDs, so it always misses, falls back to
`PLAID_ACCESS_TOKEN`, and crashes. Rather than fix this, we are retiring
webhooks in favor of scheduled sync via systemd timer.

Today `ledger sync --all` runs a complete sync pass but does NOT notify
OpenClaw agents. The server's `_background_sync()` is the only path that
calls `notify_openclaw()`. This sprint adds a `--notify` flag to the CLI so
the systemd timer can wake Hestia after syncing.

The in-process fallback loop (`CLAW_SCHEDULED_SYNC_ENABLED`) was designed as
a safety net for missed webhooks. With webhooks retired, it is also
deprecated in favor of the systemd timer.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **`CLAW_WEBHOOK_ENABLED` env var** (default `false`). When `false`,
  `POST /webhooks/plaid` returns HTTP 404 with
  `{"detail": "Webhooks are disabled. Set CLAW_WEBHOOK_ENABLED=true to enable. See RUNBOOK.md for scheduled sync setup."}`.
  When `true`, existing webhook behavior is preserved as-is (BUG-018 included).

- **`--notify` is a CLI flag on `ledger sync`**, orthogonal to `--all` /
  `--item` / default mode. It calls the existing `notify_openclaw()` from
  `notifier.py` after each successful sync that produces changes
  (`added + modified + removed > 0`). When `OPENCLAW_HOOKS_TOKEN` is not set,
  `notify_openclaw()` already logs a warning and returns — no special handling
  needed.

- **Systemd timer default is 4x/day** (`OnCalendar=*-*-* 00,06,12,18:00:00`).
  Hourly override documented as a `systemctl edit` drop-in.

- **Systemd sync service ExecStart includes `--notify`.**

- **In-process fallback loop is deprecated**, not removed. Left functional
  behind `CLAW_SCHEDULED_SYNC_ENABLED` for backward compatibility.

- **Doctor warns if both `CLAW_WEBHOOK_ENABLED=true` and
  `CLAW_SCHEDULED_SYNC_ENABLED=true`** are set simultaneously. No systemd
  timer detection — that is the operator's responsibility.

- **Deprecation documentation**: webhook code, DuckDNS, Caddy, and
  port-forward sections in ARCHITECTURE.md and RUNBOOK.md are marked
  deprecated with a note that BUG-018 is unresolved. Not removed.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before
  the next starts.
- Mark completed tasks `✅ DONE` before committing.

---

## Task 1: Gate webhooks behind `CLAW_WEBHOOK_ENABLED`

### What

Add a new env var `CLAW_WEBHOOK_ENABLED` (default `false`) to the config
layer. When disabled, the webhook endpoint returns 404. When enabled,
existing behavior is unchanged. Update doctor to warn about dual enablement.

### Config layer (`config.py`)

Add `webhook_enabled: bool = False` to the `Config` dataclass.

Parse `CLAW_WEBHOOK_ENABLED` the same way `CLAW_SCHEDULED_SYNC_ENABLED` is
parsed (`.strip().lower() == "true"`). Place it near the existing
scheduled-sync parsing block (around line 240).

### Webhook router (`routers/webhooks.py`)

At the top of the `POST /webhooks/plaid` handler, before signature
verification, load config and check `webhook_enabled`. If `false`, return:

```python
raise HTTPException(
    status_code=404,
    detail=(
        "Webhooks are disabled. Set CLAW_WEBHOOK_ENABLED=true to enable."
        " See RUNBOOK.md for scheduled sync setup."
    ),
)
```

The config is already loaded during lifespan. Store `webhook_enabled` as
module state (same pattern used for `_lifespan_config`) or pass it through
the app state, so the handler can check it without re-loading config on
every request.

### Doctor (`cli.py`)

Update `_doctor_scheduled_sync_check()` (or add a sibling function) to:

1. Report webhook status: `"doctor: webhooks: DISABLED (default)"` or
   `"doctor: webhooks: ENABLED (CLAW_WEBHOOK_ENABLED=true)"`.
2. If BOTH `webhook_enabled=True` AND `scheduled_sync_enabled=True`, emit
   a warning: `"doctor: [WARN] both webhooks and scheduled-sync are enabled — this is unusual; see RUNBOOK.md"`.

The warning is informational — it does NOT affect the exit code.

### Tests

- Webhook endpoint returns 404 when `CLAW_WEBHOOK_ENABLED` is unset/false.
- Webhook endpoint processes normally when `CLAW_WEBHOOK_ENABLED=true`.
- Doctor output includes webhook status line.
- Doctor output includes dual-enablement warning when both are true.
- Doctor output does NOT include dual-enablement warning when only one is
  enabled.

### Done when

- `POST /webhooks/plaid` returns 404 by default.
- Setting `CLAW_WEBHOOK_ENABLED=true` restores existing behavior.
- Doctor reports webhook and dual-enablement status.
- Full quality gate passes.

---

## Task 2: Add `--notify` flag to `ledger sync`

### What

Add a `--notify` flag to the `sync` CLI command. After each successful sync
that produces changes, call `notify_openclaw()`. This works with all three
sync modes: default (single-item), `--item <id>`, and `--all`.

### CLI changes (`cli.py`)

Add `--notify` to the `sync` command signature:

```python
notify: Annotated[
    int,
    typer.Option(
        "--notify", count=True,
        help="Notify OpenClaw agent after sync if new transactions arrived.",
    ),
] = 0,
```

(Uses `count=True` + `int` per the project's FBT001/FBT002 convention —
no boolean flags.)

Pass `notify > 0` down to each of the three internal helpers:
`_sync_default_mode()`, `_sync_named_item()`, `_sync_all_items()`.

### Notification logic

After each successful `run_sync()` call that returns a `SyncSummary` where
`added + modified + removed > 0`:

```python
if notify and (summary.added + summary.modified + summary.removed > 0):
    openclaw_cfg = OpenClawConfig(
        url=config.openclaw_hooks_url,
        token=config.openclaw_hooks_token,
        agent=config.openclaw_hooks_agent,
        wake_mode=config.openclaw_hooks_wake_mode,
    )
    notify_openclaw(summary, openclaw_cfg)
```

The `config` object is already loaded in each helper. `OpenClawConfig` and
`notify_openclaw` are already importable from `config.py` and `notifier.py`.

For `_sync_all_items()`: notify after EACH item that has changes, not once
at the end. This ensures Hestia is woken promptly even if a later item
fails. (This matches the server's per-sync notification behavior.)

Print a confirmation line when notification fires:
`"sync: notification sent"` (or `"sync[{item_id}]: notification sent"`).

When `--notify` is set but `OPENCLAW_HOOKS_TOKEN` is not configured,
`notify_openclaw()` already logs a warning and returns. No additional
handling is needed — just let it happen silently.

### Tests

Test the notification path in `tests/test_cli_sync.py`:

- `--notify` with changes > 0: confirm `notify_openclaw` is called
  (mock it; do not make real HTTP calls).
- `--notify` with zero changes: confirm `notify_openclaw` is NOT called.
- `--notify` with `--all` and two items (one with changes, one without):
  confirm notification fires exactly once.
- Without `--notify`: confirm `notify_openclaw` is never called regardless
  of changes.

### Done when

- `ledger sync --notify` calls `notify_openclaw()` when changes > 0.
- `ledger sync --all --notify` notifies per-item.
- `ledger sync --item x --notify` notifies for that item.
- No notification without `--notify`.
- Full quality gate passes.

---

## Task 3: Update systemd units for scheduled sync as primary

### What

Update the systemd timer to 4x/day as the default. Update the sync service
to include `--notify`. Add documentation comments reflecting the new role
as the primary sync mechanism.

### Timer (`deploy/systemd/claw-plaid-ledger-sync.timer`)

Replace `OnCalendar=hourly` with `OnCalendar=*-*-* 00,06,12,18:00:00`.

Update the `Description` to reflect the new default:
`Run claw-plaid-ledger sync four times daily`.

Update the header comment to reflect that this is now the **primary** sync
mechanism, not an alternative to webhooks.

Add a comment block documenting the hourly override:

```ini
# Default: four syncs per day (midnight, 06:00, noon, 18:00).
# To sync hourly instead, create a drop-in override:
#   systemctl edit claw-plaid-ledger-sync.timer
# and add:
#   [Timer]
#   OnCalendar=
#   OnCalendar=hourly
# The first empty OnCalendar= clears the default before setting hourly.
```

### Service (`deploy/systemd/claw-plaid-ledger-sync.service`)

Change `ExecStart` from:
```
ExecStart=%h/.local/bin/ledger sync --all
```
to:
```
ExecStart=%h/.local/bin/ledger sync --all --notify
```

Update the `Description` and header comment to reflect that this is the
primary sync mechanism and now includes agent notification.

### Tests

No automated tests — these are static unit files. Verification is:
- `OnCalendar` value is `*-*-* 00,06,12,18:00:00` in the timer.
- `ExecStart` includes `--notify` in the service.
- Comments reflect the new role.

### Done when

- Timer defaults to 4x/day with documented hourly drop-in.
- Service ExecStart includes `--notify`.
- Comments updated.
- Full quality gate passes.

---

## Task 4: Deprecate webhook and DuckDNS/Caddy documentation

### What

Mark webhook infrastructure, DuckDNS, Caddy, and port-forward documentation
as deprecated. Do NOT remove any code or documentation — mark it in place.

### ARCHITECTURE.md

Find the webhook-related sections (data flow webhook path, OpenClaw
notification from webhooks). Add a deprecation notice at the top of each:

```markdown
> **Deprecated (M25).** Webhook-based sync is disabled by default as of M25.
> The scheduled sync timer (`ledger sync --all --notify` via systemd) is now
> the primary ingestion path. Webhook code remains for operators who set
> `CLAW_WEBHOOK_ENABLED=true`, but BUG-018 (item-ID mismatch in multi-item
> setups) is unresolved. See RUNBOOK.md for migration guidance.
```

In the data flow section, add a new subsection or update the existing
scheduled-sync subsection to describe the systemd timer as the primary path.

Mark the in-process `CLAW_SCHEDULED_SYNC_ENABLED` fallback loop as
deprecated in the same section:

```markdown
> **Deprecated (M25).** The in-process fallback loop is superseded by the
> systemd timer. It remains functional for backward compatibility but is no
> longer the recommended approach.
```

### RUNBOOK.md

**Section 10 (Stable Webhook URL with DuckDNS):** Add a deprecation notice
at the top:

```markdown
> **Deprecated (M25).** This section applies only to webhook-based sync,
> which is disabled by default as of M25. If you are using the recommended
> scheduled sync timer, DuckDNS and a public URL are not required. Webhook
> code remains available behind `CLAW_WEBHOOK_ENABLED=true`, but BUG-018
> is unresolved in multi-item setups.
```

**Section 11 (Scheduled Sync Fallback):** Update to note that the in-process
loop is deprecated in favor of the systemd timer. Point readers to
Section 12.3.

**Section 12.3 (Scheduled-Sync Timer):** Update to reflect that this is now
the **primary** sync mechanism, not an alternative. Remove or soften the
"don't enable both" warning since webhooks are off by default. Add the
4x/day default and hourly drop-in instructions.

**Any Caddy/port-forward sections** referenced in the RUNBOOK that relate
to webhook ingress: add the same deprecation banner as Section 10.

### BUGS.md

No changes to BUG-018 itself — it remains active. Add a note at the top of
the BUG-018 entry:

```markdown
> **Note (M25):** Webhooks are disabled by default as of M25. This bug
> affects only operators who explicitly enable webhooks via
> `CLAW_WEBHOOK_ENABLED=true`.
```

### Done when

- ARCHITECTURE.md webhook and in-process fallback sections have deprecation
  notices.
- ARCHITECTURE.md describes systemd timer as primary sync path.
- RUNBOOK.md Sections 10, 11, 12.3, and any Caddy/port-forward sections
  have deprecation notices or are updated.
- BUGS.md BUG-018 has M25 context note.
- No code or documentation removed.
- Full quality gate passes.

---

## Task 5: Update ROADMAP.md and skill bundles

### What

Mark M25 as completed in ROADMAP.md. Update both skill bundles to reflect
that sync is now timer-driven and notification comes via `--notify`.

### ROADMAP.md

Move M25 from "Upcoming Milestones" to "Completed Milestones" with a
summary matching the style of previous entries. Include the key deliverables:
webhook gating, `--notify` flag, systemd timer as primary, doc deprecations.

### Skill bundles

**`skills/hestia-ledger/SKILL.md`:** Hestia's wake mechanism has changed.
Previously, webhooks triggered sync which triggered notification. Now the
systemd timer runs `ledger sync --all --notify`, which calls
`notify_openclaw()` directly. Update any references to the wake/trigger
mechanism. The notification payload and format are identical — only the
trigger path changed.

**`skills/athena-ledger/SKILL.md`:** Same treatment. Update any references
to webhook-driven sync to reflect timer-driven sync. Athena's scheduled
cadence is unaffected.

For both skills: if any references to `POST /webhooks/plaid` exist as part
of the data flow explanation, add a note that webhooks are deprecated and
sync is now timer-driven.

### Done when

- ROADMAP.md M25 is in Completed Milestones.
- Both skill bundles reflect timer-driven sync and `--notify` as the wake
  mechanism.
- No references to webhooks as the active/primary sync path in skill docs.
- Full quality gate passes.

---

## Acceptance criteria for Sprint 28

- `POST /webhooks/plaid` returns 404 by default; returns normal behavior
  when `CLAW_WEBHOOK_ENABLED=true`.
- `ledger sync --notify` (all three modes) calls `notify_openclaw()` when
  changes > 0.
- Systemd timer defaults to 4x/day; service ExecStart includes `--notify`.
- Doctor reports webhook status and warns on dual enablement.
- ARCHITECTURE.md, RUNBOOK.md, and BUGS.md have deprecation notices on
  webhook/DuckDNS/Caddy sections.
- In-process fallback loop marked deprecated in docs.
- M25 marked complete in ROADMAP.md.
- Skill bundles updated for timer-driven sync.
- Full quality gate (`ruff format`, `ruff check`, `mypy`, `pytest`) passes
  with no regressions.
