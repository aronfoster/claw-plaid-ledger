# Sprint 26 — BUG-019: Skill Exec Wrapper for Ledger API

## Sprint goal

Replace bare `curl` invocations in both ledger skill bundles with a
`ledger-api` wrapper script that sources credentials internally, bypassing
OpenClaw's exec-approval environment variable stripping. After this sprint
both Hestia and Athena can call the ledger API without operator approval
prompts and without exit-code-3 host-resolution failures.

## Background

OpenClaw's exec approval system (introduced in 2026.3.31) strips environment
variables from subprocess launches. `skills.entries.*.env` values are injected
into the agent's prompt context but are not propagated to child processes
spawned by the exec tool (OpenClaw issue #31583). Additionally, `$` expansion
syntax in command arguments is treated as an allowlist miss on the companion
app / node host. The combined result: `$CLAW_API_SECRET` and `$CLAW_LEDGER_URL`
resolve to empty strings when Hestia or Athena runs `curl`, producing exit
code 3 (host not found).

The fix is a `ledger-api` wrapper script installed to `/usr/local/bin/`.
The script sources `~/.openclaw/.env` inside its own process (not subject to
exec-approval env stripping), constructs the full `curl` command with auth
header and base URL, and passes additional curl arguments through. Both skill
bundles declare `binaries: [ledger-api]`; with `autoAllowSkills: true` already
set for both agents, the wrapper is auto-approved with no operator
intervention.

## Design decisions

The following decisions were made during sprint planning and must not be
re-litigated during implementation:

- **Credential source is `~/.openclaw/.env`** — This is the OpenClaw-facing
  contract (RUNBOOK Section 1c). The wrapper does not read from
  `~/.config/claw-plaid-ledger/.env`. The `.env` file is sourced inside the
  script's own bash process, which is not subject to exec-approval env
  stripping — the gate only applies to the top-level command launch
  environment.
- **Install location is `/usr/local/bin/ledger-api`** — On PATH, resolvable
  by OpenClaw's skill binary eligibility check, and accessible to both agents.
- **Both agents share the same wrapper** — One script, one install path,
  declared in both SKILL.md frontmatter files.
- **`deploy-local.sh` handles installation** — The script is copied and
  permissions set as part of the existing deploy workflow, alongside the
  `uv tool install` step.
- **Extra curl args pass through** — The wrapper accepts `-X`, `-H`, `-d`,
  etc. after the endpoint path. This avoids per-operation wrapper scripts
  (unlike the gmail-skill `bins/` pattern) because the ledger API is a
  straightforward REST surface where one generic wrapper is sufficient.
- **`CLAW_LEDGER_URL` defaults to `http://127.0.0.1:8000`** — The gateway
  runs on the same host as `ledger serve`. The wrapper reads
  `CLAW_LEDGER_URL` from the sourced `.env` if present, and falls back to
  the default if not. This makes the `skills.entries.*.env.CLAW_LEDGER_URL`
  entry in `openclaw.json` redundant (but harmless); operator cleanup is
  documented in the RUNBOOK but not required.
- **`requires.env` keeps `CLAW_API_SECRET` only** — The `CLAW_API_SECRET`
  requirement in SKILL.md frontmatter is kept because OpenClaw uses
  `requires.env` to check skill eligibility at the gateway level (where the
  variable IS present). `CLAW_LEDGER_URL` is removed from `requires.env`
  because the wrapper provides its own default. `requires.config` is removed
  because the wrapper handles file sourcing internally with a clear error
  message.
- **Stale allowlist cleanup is operator-managed** — The sprint includes
  RUNBOOK instructions for the operator to remove the `/usr/bin/curl` entry
  from `exec-approvals.json`. The developer does not modify
  `exec-approvals.json`.

## Working agreements

- Tasks are **sequential** — each must leave the quality gate green before
  the next starts.
- No API changes, no schema changes, no new endpoints.
- Mark completed tasks `✅ DONE` before committing.

---

## Task 1: Create `ledger-api` wrapper script and deploy integration

### What

Create a bash wrapper script that both agents use to call the ledger HTTP API,
and integrate it into the existing deploy workflow.

### Wrapper script (`scripts/ledger-api`)

Create `scripts/ledger-api` with the following behavior and make it executable
(`chmod +x`):

```bash
#!/usr/bin/env bash
set -euo pipefail

# Source OpenClaw env for CLAW_API_SECRET (and optionally CLAW_LEDGER_URL).
# This happens inside the script's own process — not subject to
# exec-approval env stripping.
OPENCLAW_ENV="${HOME}/.openclaw/.env"
if [[ -f "$OPENCLAW_ENV" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$OPENCLAW_ENV"
    set +a
fi

CLAW_LEDGER_URL="${CLAW_LEDGER_URL:-http://127.0.0.1:8000}"

if [[ -z "${CLAW_API_SECRET:-}" ]]; then
    echo "ledger-api: CLAW_API_SECRET is not set (checked $OPENCLAW_ENV)" >&2
    exit 2
fi

if [[ $# -lt 1 ]]; then
    echo "Usage: ledger-api <endpoint-path> [curl-args...]" >&2
    echo "  ledger-api /health" >&2
    echo "  ledger-api /transactions?range=last_30_days" >&2
    echo "  ledger-api /transactions/ID/allocations -X PUT -d '[...]'" >&2
    exit 2
fi

ENDPOINT="$1"
shift

exec curl -s \
    -H "Authorization: Bearer ${CLAW_API_SECRET}" \
    "${CLAW_LEDGER_URL}${ENDPOINT}" \
    "$@"
```

Key behaviors:

- `set -euo pipefail` for strict error handling.
- Sources `~/.openclaw/.env` with `set -a` / `set +a` to export all variables.
- `# shellcheck source=/dev/null` suppresses the expected SC1091 on the
  dynamic source path.
- Validates `CLAW_API_SECRET` is non-empty; exits 2 with a diagnostic message
  naming the checked file.
- Defaults `CLAW_LEDGER_URL` to `http://127.0.0.1:8000` if not set.
- First positional arg is the endpoint path (must start with `/`).
- Remaining args pass through to `curl` verbatim (`"$@"`).
- Uses `exec` to replace the shell process with curl (clean PID, correct
  signal handling, exit code propagation).

### Deploy integration (`scripts/deploy-local.sh`)

Add to `scripts/deploy-local.sh`, after the `uv tool install` step and before
the `systemctl restart`:

```bash
echo "Installing ledger-api wrapper..."
sudo install -m 755 "$(dirname "$0")/ledger-api" /usr/local/bin/ledger-api
```

`install -m 755` copies the file and sets permissions atomically. Use
`$(dirname "$0")` so the path resolves correctly regardless of the caller's
working directory.

### Done when

- `scripts/ledger-api` exists, is executable, and is well-formed bash (no
  syntax errors; `bash -n scripts/ledger-api` exits 0).
- `scripts/deploy-local.sh` includes the `install` step.
- Full quality gate passes (the script is bash, not Python, so ruff/mypy/pytest
  are unaffected — but the gate must still be green with no regressions).

---

## Task 2: Update skill bundles

### What

Update both `skills/hestia-ledger/SKILL.md` and `skills/athena-ledger/SKILL.md`
to replace all `curl` invocations with `ledger-api` and update frontmatter to
declare the new binary.

### SKILL.md frontmatter changes (both skills)

Replace the `requires`, `binaries`, and `doctor` sections. The resulting
`metadata.openclaw` block for **both** skills should look like this (emoji
differs per skill — `🧾` for Hestia, `📊` for Athena):

```yaml
metadata:
  openclaw:
    emoji: '<per-skill emoji>'
    requires:
      env:
        - CLAW_API_SECRET
    primaryEnv: CLAW_API_SECRET
    binaries:
      - ledger-api
    doctor: 'ledger-api /health'
```

Changes from the current frontmatter:

1. **`binaries`**: `curl` → `ledger-api`.
2. **`doctor`**: the full `curl -s -H "..." "$CLAW_LEDGER_URL/health"` →
   `ledger-api /health`.
3. **`requires.env`**: remove `CLAW_LEDGER_URL` (wrapper provides default).
   Keep `CLAW_API_SECRET` (needed for skill eligibility check at gateway
   level).
4. **`requires.config`**: remove entirely (wrapper handles file sourcing
   internally).
5. **`requires.bins`**: not present in current files; do not add (the
   `binaries` key at the `openclaw` level already serves this purpose).

### "Making API calls" section (both skills)

Replace the current section in both SKILL.md files. Current text:

```markdown
## Making API calls

`$CLAW_API_SECRET` and `$CLAW_LEDGER_URL` are already in your environment. No `source`, no shell wrapper, no pipes.

\`\`\`bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" "$CLAW_LEDGER_URL/transactions?range=last_30_days"
\`\`\`
```

Replace with:

```markdown
## Making API calls

Use `ledger-api` for all ledger HTTP calls. It handles auth and base URL
internally — no env vars, no `source`, no pipes.

\`\`\`bash
# GET (default)
ledger-api /transactions?range=last_30_days

# GET with filters
ledger-api "/transactions?tags=needs-athena-review&start_date=2026-01-01&end_date=2026-03-31"

# PUT with JSON body
ledger-api /transactions/abc123/allocations \
  -X PUT -H "Content-Type: application/json" \
  -d '[{"amount": 12.34, "category": "groceries", "tags": ["household"]}]'
```

Do not call `curl` directly. Do not use `source`, env-var expansion, or shell
pipes in API calls.
```

### Doctor section

If either SKILL.md contains a `## Doctor` section (added during BUG-016),
update the example command from the bare-curl form to `ledger-api /health`.
If the section references the `source … | python3 -c` prohibition, keep
that prohibition and extend it: "Do not use `curl` directly, `source`,
`python3 -c`, or shell pipes."

### Done when

- Both SKILL.md files have updated frontmatter with `binaries: [ledger-api]`
  and `doctor: 'ledger-api /health'`.
- Both SKILL.md files have a "Making API calls" section showing `ledger-api`
  usage and explicitly prohibiting direct `curl`.
- `grep -r "\\bcurl\\b" skills/` returns no hits except:
  - The `exec curl` line inside the wrapper script itself (if it were in
    skills/ — it is not, it is in `scripts/`).
  - Historical/explanatory text that says "Do not call `curl` directly" or
    similar prohibitions.
- Full quality gate passes.

---

## Task 3: Update documentation

### What

Update project documentation to reflect the wrapper script, provide operator
cleanup instructions, and mark BUG-019 resolved.

### ARCHITECTURE.md

**Repository layout**: Add `scripts/ledger-api` to the `scripts/` section:

```
scripts/
  deploy-local.sh   # reinstall ledger via uv tool install and restart the systemd service
  duckdns-update.sh # DuckDNS IP-update script for cron/systemd
  install-hooks.sh
  ledger-api        # OpenClaw agent wrapper for ledger HTTP API (installed to /usr/local/bin)
  sync-skills.sh    # push/pull OpenClaw agent skill bundles between repo and ~/.openclaw
```

**Operator handoff**: In the "Operator handoff" section (near "Skill install
source"), add a bullet or short paragraph:

> **Agent API access**: Both skill bundles use `ledger-api` (a bash wrapper
> installed to `/usr/local/bin/ledger-api`) for all HTTP calls. The wrapper
> sources `~/.openclaw/.env` for `CLAW_API_SECRET` and defaults
> `CLAW_LEDGER_URL` to `http://127.0.0.1:8000`. This bypasses OpenClaw's
> exec-approval env stripping. The wrapper is deployed automatically by
> `scripts/deploy-local.sh`.

### RUNBOOK.md

**Section 1c (Two-agent OpenClaw setup)**: After the `./scripts/sync-skills.sh
push` step and before the `cat >> ~/.openclaw/.env` block, add:

> **Deploy the API wrapper.** The `ledger-api` wrapper script is installed
> automatically by `scripts/deploy-local.sh`. If you have not run deploy
> recently, install it manually:
>
> ```bash
> sudo install -m 755 scripts/ledger-api /usr/local/bin/ledger-api
> ```
>
> Verify it works:
>
> ```bash
> ledger-api /health
> # Expected: {"status": "ok"}
> ```

**New subsection in Section 1c or a new Section 23**: Add an operator cleanup
checklist:

> ### Post-upgrade cleanup (after Sprint 26)
>
> After deploying the `ledger-api` wrapper and pushing updated skill bundles,
> clean up stale exec-approval and config entries:
>
> **1. Remove stale curl allowlist entry from `exec-approvals.json`.**
>
> Open `~/.openclaw/exec-approvals.json` and remove the `/usr/bin/curl`
> entry from the `agents.hestia.allowlist` array. The entry looks like:
>
> ```json
> {
>   "id": "5d35405b-...",
>   "pattern": "/usr/bin/curl",
>   ...
> }
> ```
>
> Remove the entire object (and any trailing comma). Also remove the
> `/usr/bin/echo` entry if it was only used for skill debugging. Leave
> the `allowlist` key as an empty array `[]` if no other entries remain.
> Restart the gateway after editing: `openclaw gateway restart`.
>
> **2. (Optional) Remove redundant `CLAW_LEDGER_URL` from `openclaw.json`
> skill entries.**
>
> The `ledger-api` wrapper defaults `CLAW_LEDGER_URL` to
> `http://127.0.0.1:8000`. The `skills.entries.*.env.CLAW_LEDGER_URL`
> values in `~/.openclaw/openclaw.json` are now redundant. You can remove
> the `env` block from both `hestia-ledger` and `athena-ledger` entries:
>
> ```json
> "hestia-ledger": {
>   "apiKey": { "source": "env", "provider": "default", "id": "CLAW_API_SECRET" }
> }
> ```
>
> This is optional — the redundant entry is harmless.
>
> **3. Verify end-to-end.**
>
> Start a new Hestia session and ask her to run a health check. She should
> call `ledger-api /health` with no approval prompts and receive
> `{"status": "ok"}`. Repeat for Athena.

### BUGS.md

Update BUG-019 status block. Move from **Active bugs** to **Resolved bugs**
with the following status and fix summary:

> **Status:** Resolved (Sprint 26)
> **Severity:** High (both agents unable to call ledger API without manual
> per-run operator approval; env vars not propagated to exec subprocesses)
> **Area:** Skill definitions (`hestia-ledger/SKILL.md`,
> `athena-ledger/SKILL.md`) / OpenClaw exec-approval env propagation
>
> #### Root cause
>
> OpenClaw's exec approval system does not propagate `skills.entries.*.env`
> variables to subprocesses spawned by the exec tool (OpenClaw issue #31583).
> Additionally, `$` expansion syntax in command arguments is treated as an
> allowlist miss. The combined effect: `$CLAW_API_SECRET` and
> `$CLAW_LEDGER_URL` resolve to empty strings when agents run `curl`,
> producing exit code 3 (host not found).
>
> #### Fix
>
> Added `scripts/ledger-api`, a bash wrapper that sources `~/.openclaw/.env`
> inside its own process, constructs the authenticated `curl` command with
> base URL, and passes additional arguments through. Installed to
> `/usr/local/bin/ledger-api` via `scripts/deploy-local.sh`. Both skill
> bundles updated: `binaries: [ledger-api]`, `doctor: 'ledger-api /health'`,
> all API call examples replaced. With `autoAllowSkills: true` already
> configured for both agents, the wrapper is auto-approved with no operator
> intervention.

### README.md

No changes required — the README's "Two-agent skill bundle quickstart" section
references `sync-skills.sh push` which will copy the updated skill files. The
operator RUNBOOK covers `deploy-local.sh` which installs the wrapper.

If the developer judges that a one-line mention of `ledger-api` in the
"Two-agent skill bundle quickstart" section would help discoverability, a
note like "Run `bash scripts/deploy-local.sh` to install the `ledger-api`
wrapper used by both skills" is acceptable but not required.

### Done when

- `ARCHITECTURE.md` repository layout includes `scripts/ledger-api`.
- `RUNBOOK.md` documents the wrapper installation and operator cleanup steps.
- `BUGS.md` has BUG-019 moved to resolved with the root-cause and fix summary.
- `grep -r "\\bcurl\\b" skills/` returns no hits except prohibition text
  ("Do not call `curl` directly" or similar).
- Full quality gate passes.

---

## Acceptance criteria for Sprint 26

- `scripts/ledger-api` exists, is executable, and correctly calls the ledger
  API when `CLAW_API_SECRET` is set in `~/.openclaw/.env`.
- `scripts/deploy-local.sh` installs `ledger-api` to `/usr/local/bin/`.
- Both skill bundles declare `binaries: [ledger-api]` and
  `doctor: 'ledger-api /health'`.
- No skill file instructs agents to call `curl` directly.
- BUGS.md has BUG-019 resolved.
- RUNBOOK.md documents wrapper installation and operator cleanup
  (stale allowlist entry removal, optional `openclaw.json` simplification).
- Full quality gate (`ruff format`, `ruff check`, `mypy`, `pytest`) passes
  with no regressions.
