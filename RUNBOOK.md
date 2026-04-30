# Production Operations Runbook

## 1. Purpose and scope

This runbook covers the steps an operator needs to move
`claw-plaid-ledger` from Plaid sandbox to a live production environment,
validate the setup before the first real sync, and operate the service
durably on a home server.

**In scope:**

- Obtaining Plaid production API access
- Connecting institutions via the `ledger link` browser flow
- Household source precedence setup (`suppressed_accounts`,
  `ledger apply-precedence`, `ledger overlaps`)
- Configuring and isolating the production environment
- Running `ledger doctor --production-preflight` before first live sync
- Daily item health checks via `ledger items` and `ledger sync --all`
- Canonical-vs-raw transaction view behavior for agent/API consumers
- Multi-item webhook routing and scheduled sync fallback
- Stable public webhook URL setup with DuckDNS
- Request/sync correlation-ID tracing in logs
- Webhook ingress IP allowlisting (Section 9.6)
- systemd service and timer deployment (Section 12)
- Container deployment via Docker Compose or Proxmox LXC (Section 13)
- Reverse-proxy auth hardening with Caddy mTLS or Authelia OIDC (Section 14)
- Deployment selection guide (Section 15)
- Agent skill auto-registration via `sync-skills.sh push` (Section 16)
- Account labels and enriched spend queries (Section 17)
- Month-over-month spend trends via `GET /spend/trends` (Section 18)
- Ledger error log monitoring via `GET /errors` (Section 19)
- Allocation model and budgeting layer (Section 20)
- Multi-allocation editing and split transactions (Section 21)
- On-demand Plaid refresh via `ledger refresh` (Section 22)
- Performing a first live sync and validating the result
- Backup and recovery procedures for SQLite and secrets
- Incident triage quick reference

**Explicitly out of scope (deferred):**

- Automated background re-link / re-auth detection

---

## 1b. Daily operations

### Check item health

Run this before or after a sync to confirm all configured items are
accessible:

```bash
ledger items
```

Expected output when all tokens are set:

```
items: bank-alice  owner=alice  token=SET  accounts=3  last_synced=2026-03-10T14:22:00+00:00
items: card-alice  owner=alice  token=SET  accounts=1  last_synced=2026-03-10T14:23:00+00:00
items: card-bob    owner=bob    token=SET  accounts=2  last_synced=2026-03-10T14:24:00+00:00
items: 3/3 items healthy, 0 need attention
```

Items listed as `token=MISSING` need their access-token env var set before
`ledger sync --all` can reach them.  See `items.toml.example` at the repo root
for the recommended household configuration format.

### Sync all items

The standard household ingestion path is:

```bash
ledger sync --all
```

This reads every item from `~/.config/claw-plaid-ledger/items.toml`,
fetches the latest transactions from Plaid for each one, and writes them
to SQLite.  Per-item failures are isolated — one bad token does not
stop the other items from syncing.

Single-item mode (`ledger sync` with `PLAID_ACCESS_TOKEN`) remains valid
for simple single-institution setups.



### Household source precedence setup

Use this flow after all household items are linked and synced at least once.

1. Sync all configured items so every account exists in SQLite:

```bash
ledger sync --all
```

2. Add suppression mappings to `~/.config/claw-plaid-ledger/items.toml`:

```toml
[[items]]
id = "bank-alice"
access_token_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
owner = "alice"

  [[items.suppressed_accounts]]
  plaid_account_id = "plaid_acct_shared_alice_view"
  canonical_account_id = "plaid_acct_shared_bob_view"
  canonical_from_item = "card-bob"
  note = "Shared card: bob's institution is canonical"
```

3. Apply mappings to the DB:

```bash
ledger apply-precedence
```

4. Verify status and look for missing overlap mappings:

```bash
ledger overlaps
```

Expected operator outcome:
- configured suppressions show `IN DB` once synced and applied
- not-yet-synced mappings show `NOT YET SYNCED`
- stale DB mappings show `MISMATCH` until `apply-precedence` is re-run

Canonical behavior reminder:
- `GET /transactions` defaults to canonical household view
- `GET /transactions?view=raw` returns all rows, including suppressed accounts
- `GET /transactions/{id}` includes `suppressed_by` provenance for suppressed
  raw transactions

---

## 1c. Two-agent OpenClaw setup

Push both skill bundles into the agent workspace directories and register
them in each agent's `TOOLS.md` in one step:

```bash
./scripts/sync-skills.sh push
```

See Section 16 for what this does and how to verify it.

**Deploy the API wrapper.** `scripts/deploy-local.sh` installs `ledger-api`
to `/usr/local/bin/ledger-api` automatically. If you have not run deploy
recently, install it manually:

```bash
sudo install -m 755 scripts/ledger-api /usr/local/bin/ledger-api
```

Verify it works:

```bash
ledger-api /health
# Expected: {"status": "ok"}
```

Add the ledger credentials to `~/.openclaw/.env` so that OpenClaw loads
them automatically at startup — no flags or manual exports required:

```bash
cat >> ~/.openclaw/.env <<'EOF'
CLAW_API_SECRET=<your-CLAW_API_SECRET-value>
CLAW_LEDGER_URL=http://127.0.0.1:8000
EOF
chmod 600 ~/.openclaw/.env
```

`CLAW_API_SECRET` must match the value in
`~/.config/claw-plaid-ledger/.env`.  `CLAW_LEDGER_URL` is the base URL
of the running `ledger serve` instance; adjust the port if you changed
`CLAW_SERVER_PORT`.

OpenClaw reads `~/.openclaw/.env` on every startup (lower precedence
than the process environment, so existing shell exports are never
overridden).  Because the vars are in the environment when the gateway
starts, the `env`-source SecretRef objects in `openclaw.json` resolve
them automatically — no `--env-file` flags are needed.

Register both skills in `~/.openclaw/openclaw.json` so OpenClaw injects
the credentials into each agent's session.  Without this step the skills
are discovered (files are present) but not **eligible** — the required
env vars (`CLAW_API_SECRET`, `CLAW_LEDGER_URL`) are never injected, so
the skills do not appear in the agent's system prompt and the agent
reports not having them.

**Use the `SecretRef` object form for `apiKey` — do not use a plaintext
value or `"${VAR}"` string interpolation.**  Multiple OpenClaw CLI
operations (`doctor --fix`, `update`, `config.patch`) have historically
resolved `${...}` references to plaintext at write time, permanently
baking secrets into the config file on disk (openclaw/openclaw issues
#4654, #9627, #15932).  The `SecretRef` object form is not subject to
this class of bug — OpenClaw preserves the reference object on
write-back and resolves the secret in memory at activation time only.

**Restrict each skill to its intended agent** using the
`agents.list[].skills` allowlist.  OpenClaw treats this field as an
allowlist: only skills named in the array are eligible for that agent.
Omitting the field entirely grants the agent access to all registered
skills.  Without this step both agents can see both skills, which is
undesirable — Hestia should never be offered `athena-ledger`, and vice
versa.

If `~/.openclaw/openclaw.json` does not exist yet, create it with the
full block below.  If it already exists, merge the `skills` key and the
`agents.list[].skills` allowlists into the existing JSON manually (or
use `jq` — see the note below).

```json
{
  "agents": {
    "list": [
      {
        "id": "main",
        "skills": [
          "athena-ledger"
        ]
      },
      {
        "id": "hestia",
        "skills": [
          "hestia-ledger"
        ],
        "tools": {
          "allow": ["exec"]
        }
      }
    ]
  },
  "skills": {
    "entries": {
      "hestia-ledger": {
        "apiKey": {
          "source": "env",
          "provider": "default",
          "id": "CLAW_API_SECRET"
        },
        "env": {
          "CLAW_LEDGER_URL": "http://127.0.0.1:8000"
        }
      },
      "athena-ledger": {
        "apiKey": {
          "source": "env",
          "provider": "default",
          "id": "CLAW_API_SECRET"
        },
        "env": {
          "CLAW_LEDGER_URL": "http://127.0.0.1:8000"
        }
      }
    }
  }
}
```

`apiKey` maps to the skill's declared `primaryEnv` (`CLAW_API_SECRET`).
The `{ source, provider, id }` SecretRef tells OpenClaw to read the
value from the named environment variable at activation time — the
plaintext secret is never written to disk.  `CLAW_API_SECRET` must be
present in the shell environment (or the agent's process environment)
when OpenClaw starts.

`env.CLAW_LEDGER_URL` is a non-sensitive local URL and is safe as a
literal string.

> **jq one-liner (if the file already exists and contains valid JSON):**
> ```bash
> jq '.skills.entries["hestia-ledger"] = {
>       apiKey: {source:"env",provider:"default",id:"CLAW_API_SECRET"},
>       env: {CLAW_LEDGER_URL:"http://127.0.0.1:8000"}} |
>     .skills.entries["athena-ledger"] = {
>       apiKey: {source:"env",provider:"default",id:"CLAW_API_SECRET"},
>       env: {CLAW_LEDGER_URL:"http://127.0.0.1:8000"}} |
>     (.agents.list[] | select(.id == "main")).skills = ["athena-ledger"] |
>     (.agents.list[] | select(.id == "hestia")).skills = ["hestia-ledger"] |
>     (.agents.list[] | select(.id == "hestia")).tools = {"allow":["exec"]}' \
>    ~/.openclaw/openclaw.json > /tmp/openclaw.json \
>    && mv /tmp/openclaw.json ~/.openclaw/openclaw.json
> ```

After writing `openclaw.json`, verify no plaintext secrets leaked into
the file or runtime artifacts:

```bash
openclaw secrets audit --check
```

A clean audit shows no plaintext findings.  If any are reported, run
`openclaw secrets configure` to interactively re-map affected credentials
to SecretRefs, then re-run the audit.

After saving `openclaw.json`, start a **new** OpenClaw session for each
agent — OpenClaw snapshots eligible skills at session start and the
updated config will not take effect in an already-running session.

Start the ledger server before invoking either agent skill:

```bash
uv run --locked ledger serve
```

Recommended run pattern:

- **Hestia**: event-driven; woken by `ledger sync --all --notify` via systemd
  timer (4x/day default). See Section 12.3.
- **Athena**: periodic review (daily/weekly) and targeted checks for
  `needs-athena-review` transactions.

Operational check:

- Ensure `OPENCLAW_HOOKS_AGENT=Hestia` so scheduled sync wakes the ingestion
  worker first; Athena should not be woken on every sync by default.

## 2. Plaid production-access prerequisites

### 2.1 Plaid dashboard checklist

Complete each item in the Plaid dashboard before running any production
command:

- [ ] Application is approved for **Production** environment access.
- [ ] The **Production** `client_id` and `secret` are noted — they are
      different from the sandbox credentials.
- [ ] At least one **Link session** has been completed for each institution
      you intend to sync.  Each session produces one `access_token`
      (live credential, treat as a secret).
- [ ] A **webhook URL** is configured if you intend to receive push
      notifications from Plaid (`POST /webhooks/plaid` on the running
      server).  Home servers need a stable public URL — see
      **Section 10 — Stable webhook URL with DuckDNS** for setup
      instructions.
- [ ] The **webhook signing secret** is noted from the dashboard
      (Webhooks → Signing secret).  This is the `PLAID_WEBHOOK_SECRET`
      value.

### 2.2 Required Plaid production credentials

| Credential | Where to find it | Env var |
|---|---|---|
| Client ID | Dashboard → Team Settings → Keys | `PLAID_CLIENT_ID` |
| Production secret | Dashboard → Team Settings → Keys | `PLAID_SECRET` |
| Access token(s) | Returned by `ledger link` flow, store securely | `PLAID_ACCESS_TOKEN` (single-item) or per-item env var (multi-item) |
| Webhook signing secret | Dashboard → Webhooks → Signing secret | `PLAID_WEBHOOK_SECRET` |

> **Do not use sandbox credentials for production syncs.**  The
> `PLAID_ENV` value must be `production` (not `sandbox`) when using live
> bank connections.

### 2.3 Connecting an institution with `ledger link`

`ledger link` starts a temporary local HTTP server, opens the browser,
guides the operator through the Plaid Link flow, and prints the resulting
`access_token` and a ready-to-paste `items.toml` snippet:

```bash
ledger link
# Creating Plaid link token...
# Starting local Link server at http://127.0.0.1:18790
# Opening browser — complete the Plaid Link flow to connect your institution.
#
# Link complete. Exchanging token...
#
#   access_token : access-production-xxxxxxxxxxxxxxxxxxxxxxxx
#   item_id      : XXXXXXXXXXXXXXXXXXXXXXXXXX
#
# Add to items.toml and set the matching env var:
#
#   [[items]]
#   id                = "bank-alice"
#   access_token_env  = "PLAID_ACCESS_TOKEN_BANK_ALICE"
#   owner             = "alice"
#
#   export PLAID_ACCESS_TOKEN_BANK_ALICE="access-production-xxxx..."
```

Run `ledger link` once per institution.  Each completed Link flow
produces one `access_token`; store it in the `~/.config/claw-plaid-ledger/.env`
file and add the corresponding `[[items]]` block to `items.toml`.

Optional flags:

```bash
ledger link --products transactions --products investments
```

The `--products` flag may be passed multiple times (default: `transactions`).

Pass `--webhook` to register the webhook URL on the new item at link time,
so no separate `ledger webhook-set` call is needed afterward:

```bash
ledger link --webhook https://<subdomain>.duckdns.org:8443/webhooks/plaid
```

Use `ledger link --help` for the full option list.

See `items.toml.example` at the repo root for a two-person household
configuration example.

---

## 3. Cost model

### 3.1 What events are billable

Plaid bills per **item** (institution link), not per API call or sync
run.  The primary cost events are:

- **Creating a new item** (completing a Plaid Link flow for an
  institution) — billed once per institution per user.
- **Item reactivation** after forced re-auth (e.g. password change,
  revoked token) — billed as a new item creation in some plans.
- **Transactions API calls** — some plans include a volume of free
  calls; check your agreement.

### 3.2 Sync frequency is not the primary billing lever

Running `ledger sync` or `ledger sync --all` more frequently does
**not** significantly increase your Plaid bill.  The sync engine is
cursor-based and idempotent — re-running against an already-current
cursor returns an empty result at minimal cost.

The cost that matters is **how many items (institution links) you
create**, not how often you poll them.

### 3.3 Avoiding accidental cost spikes

- **Do not run Link flows repeatedly** for the same institution.  A
  single completed Link session per institution is enough.  Store the
  resulting `access_token` securely and reuse it indefinitely.
- **Revoke tokens intentionally.**  Calling Plaid's item removal endpoint
  or removing an institution from the dashboard destroys the item.
  Re-linking creates a new billable item.
- **Test Link flows in sandbox first.**  Sandbox Link uses fake
  institutions and is free.  Validate your Link integration there before
  going live.
- **Audit your items count regularly** via the Plaid dashboard (Products
  → Transactions → Items) to catch abandoned links.

---

## 4. Access-token lifecycle

### 4.1 Secure persistence

An `access_token` is a long-lived credential that grants read access to
a user's bank data.  Treat it with the same care as a password.

**Required:**

- Store each `access_token` exclusively in your user config file
  (`~/.config/claw-plaid-ledger/.env`) or as a named environment
  variable listed in `items.toml`.
- The config directory must be mode `700`; the `.env` file mode `600`.
- Never store tokens in:
  - This git repository or any committed file
  - The OpenClaw workspace directory (agent-readable exports)
  - Log files or command history

```bash
# Correct permissions — run once after creating the config dir:
chmod 700 ~/.config/claw-plaid-ledger
chmod 600 ~/.config/claw-plaid-ledger/.env
```

### 4.2 Revocation and re-link scenarios

| Scenario | What happens | Recovery |
|---|---|---|
| User revokes access via bank | `access_token` becomes invalid; Plaid sends `ITEM_LOGIN_REQUIRED` webhook | Re-run Plaid Link update mode; new token replaces old one |
| User changes bank password | Token may become invalid | Re-run Plaid Link update mode |
| You call Plaid item-remove endpoint | Token is permanently destroyed | Run a new Link flow; this creates a new billable item |
| Token rotated by Plaid | Plaid fires `PENDING_EXPIRATION` then `USER_PERMISSION_REVOKED` | Plaid provides a replacement token automatically in most cases; check webhook payload |

> **Keep historical data.**  Revoking or re-linking an item does **not**
> delete rows already written to SQLite.  Your local ledger is
> independent of the live Plaid connection.

### 4.3 Forced re-auth edge cases

If Plaid returns HTTP 400 with error code `ITEM_LOGIN_REQUIRED`:

1. Do **not** delete the existing `access_token` env var yet.
2. Run Plaid Link in update mode to re-authenticate.
3. Replace the env var value with the new `access_token` returned by Link.
4. Re-run `ledger doctor --production-preflight` to confirm the new
   token env var is present.
5. Run `ledger sync` (or `ledger sync --all`) to resume normal ingestion.

---

## 5. Sandbox vs production isolation

### 5.1 Required environment separation

The production and sandbox environments must never share configuration
or data.

| Setting | Sandbox | Production |
|---|---|---|
| `PLAID_ENV` | `sandbox` | `production` |
| `PLAID_CLIENT_ID` | Sandbox client ID | **Different** production client ID |
| `PLAID_SECRET` | Sandbox secret | **Different** production secret |
| `PLAID_ACCESS_TOKEN` | Sandbox token (fake data) | Live bank token |
| `CLAW_PLAID_LEDGER_DB_PATH` | e.g. `~/ledger-sandbox.db` | e.g. `~/ledger.db` |

Use separate `.env` files or shell profiles for each environment.
Never mix production secrets into a sandbox config.

### 5.2 DB and path separation

Use distinct SQLite file paths for sandbox and production:

```bash
# Sandbox
export CLAW_PLAID_LEDGER_DB_PATH=~/.local/share/claw-plaid-ledger/ledger-sandbox.db

# Production
export CLAW_PLAID_LEDGER_DB_PATH=~/.local/share/claw-plaid-ledger/ledger.db
```

Writing live data into a sandbox DB (or vice versa) corrupts the
transaction history.  Treat the two DBs as completely separate systems.

### 5.3 Preflight checks before first live sync

Run the production preflight immediately after configuring production
credentials, before calling any live Plaid endpoint:

```bash
ledger doctor --production-preflight
```

All required checks must report `[PASS]` before proceeding.  A `[WARN]`
for `PLAID_ENV_SANDBOX` is a signal that you may still be pointing at
the sandbox environment — verify `PLAID_ENV=production` is set.

---

## 6. Migration and first-live-sync checklist

Follow these steps in order.

### Step 1 — Set up the production config directory

```bash
mkdir -p ~/.config/claw-plaid-ledger
chmod 700 ~/.config/claw-plaid-ledger
cp .env.example ~/.config/claw-plaid-ledger/.env
chmod 600 ~/.config/claw-plaid-ledger/.env
```

### Step 2 — Populate production credentials

Edit `~/.config/claw-plaid-ledger/.env` with your production values:

```bash
PLAID_CLIENT_ID=<your-production-client-id>
PLAID_SECRET=<your-production-secret>
PLAID_ENV=production
PLAID_ACCESS_TOKEN=<access-token-from-link-flow>  # single-item mode
CLAW_PLAID_LEDGER_DB_PATH=~/.local/share/claw-plaid-ledger/ledger.db
CLAW_API_SECRET=<strong-random-secret>
PLAID_WEBHOOK_SECRET=<webhook-signing-secret-from-dashboard>
```

For multi-item mode, populate `~/.config/claw-plaid-ledger/items.toml`
and set each access-token env var instead.

### Step 3 — Run production preflight

```bash
ledger doctor --production-preflight
```

Expected output when ready:

```
preflight: PLAID_CLIENT_ID [PASS] PLAID_CLIENT_ID is set
preflight: PLAID_SECRET [PASS] PLAID_SECRET is set
preflight: PLAID_ENV [PASS] PLAID_ENV is set
preflight: CLAW_API_SECRET [PASS] CLAW_API_SECRET is set
preflight: CLAW_PLAID_LEDGER_DB_PATH [PASS] DB path ... (run 'ledger init-db' ...)
preflight: items.toml [PASS] items.toml not found or empty — single-item mode
preflight: PLAID_ENV_SANDBOX [PASS] PLAID_ENV='production' (not sandbox)
preflight: all required checks passed
```

If any `[FAIL]` lines appear, fix them before continuing.

### Step 4 — Initialise the database

```bash
ledger init-db
```

Confirm the file was created:

```bash
ls -lh ~/.local/share/claw-plaid-ledger/ledger.db
```

### Step 5 — Run standard doctor check

```bash
ledger doctor
```

All lines should show `[OK]`.  `CLAW_API_SECRET [FAIL]` is acceptable
if you are not running the HTTP server yet.

### Step 6 — First live sync (mandatory)

```bash
ledger sync          # single-item mode
# or
ledger sync --all    # multi-item mode via items.toml
```

Expected output (single-item):

```
sync: accounts=N added=N modified=0 removed=0
```

Where `N > 0` for accounts confirms the live connection is working.

### Step 7 — Validate sync results

```bash
ledger doctor
```

Check that `sync_state rows=1` (or more for multi-item) and
`last_synced_at` is a recent timestamp.

### Step 8 — (Optional) Start the HTTP server

Before starting `ledger serve`, confirm your intent for the scheduled sync
fallback:

- **Webhooks only (default):** leave `CLAW_SCHEDULED_SYNC_ENABLED` unset or
  set to `false`.  No background loop is started.
- **Scheduled fallback enabled:** set `CLAW_SCHEDULED_SYNC_ENABLED=true`
  (and optionally `CLAW_SCHEDULED_SYNC_FALLBACK_HOURS`).  The loop starts
  automatically and runs every 60 minutes.  Run `ledger doctor` to confirm
  the reported state before and after enabling.

For a stable public webhook URL (required for Plaid to deliver events to a
home server), see **Section 10 — Stable webhook URL with DuckDNS** below.

```bash
ledger serve
```

Confirm `/health` returns `{"status": "ok"}`:

```bash
curl http://127.0.0.1:8000/health
```

---

## 7. Backup and recovery

### 7.1 SQLite database backup

The SQLite file at `CLAW_PLAID_LEDGER_DB_PATH` is the complete local
ledger.  The default location (when `CLAW_PLAID_LEDGER_DB_PATH` is not
set) is:

```
~/.local/share/claw-plaid-ledger/ledger.db
```

Back it up with:

```bash
# Safe online backup (SQLite's built-in copy mechanism):
sqlite3 "$CLAW_PLAID_LEDGER_DB_PATH" ".backup $HOME/ledger-backup-$(date +%Y%m%d).db"

# Or simply copy the file when the server is not running:
cp "$CLAW_PLAID_LEDGER_DB_PATH" "$HOME/ledger-backup-$(date +%Y%m%d).db"
```

Automate this with a cron job or systemd timer.  Daily backups are
sufficient for most household use cases.

**Offsite backup:** `ledger.db` should be included in any encrypted
offsite backup alongside secrets.  The reference setup uses a GPG-encrypted
tarball uploaded to Google Drive via `rclone` — see
`~/.openclaw/scripts/backup-financial.sh` for the implementation.

**Recovery:** Replace the DB file with your backup.  Re-run
`ledger sync` to pull in any transactions that occurred since the
backup.

### 7.2 Secrets and config file backup

Back up the entire config directory:

```bash
tar -czf ~/claw-config-backup-$(date +%Y%m%d).tar.gz \
    ~/.config/claw-plaid-ledger/
chmod 600 ~/claw-config-backup-$(date +%Y%m%d).tar.gz
```

Store the backup in an encrypted location (e.g. a password manager's
secure notes, an encrypted external drive, or a secrets vault).

> **Warning:** The backup archive contains live Plaid access tokens.
> Treat it with the same care as a password database.

**Recovery:** Extract the archive to restore your config:

```bash
tar -xzf ~/claw-config-backup-<date>.tar.gz -C ~/
chmod 700 ~/.config/claw-plaid-ledger
chmod 600 ~/.config/claw-plaid-ledger/.env
ledger doctor --production-preflight
```

---

## 8. Command reference (operations)

| Command | When to use it |
|---|---|
| `ledger items` | Quick daily health check (token presence, account counts, last sync) |
| `ledger sync --all` | Standard household ingestion run across all configured items |
| `ledger apply-precedence` | Persist `suppressed_accounts` source-precedence mappings into SQLite |
| `ledger overlaps` | Verify suppression status and discover potential unconfirmed overlaps |
| `ledger doctor --production-preflight` | Validate production-readiness config without external calls |
| `ledger doctor` | Validate local DB/config health after syncs and changes |
| `ledger refresh --all` | Ask Plaid to re-check all institutions and confirm `SYNC_UPDATES_AVAILABLE` webhook delivery |
| `ledger refresh --item <id>` | Ask Plaid to re-check a single institution from `items.toml` |

---

## 9. Incident appendix

### 9.1 Invalid or expired access token

**Symptoms:** Sync fails with a Plaid error referencing
`ITEM_LOGIN_REQUIRED` or `INVALID_ACCESS_TOKEN`.

**Triage:**

1. Check the `PLAID_ACCESS_TOKEN` (single-item) or the relevant per-item
   env var is set and not empty:
   ```bash
   ledger doctor --production-preflight
   ```
2. If the env var is present but sync still fails, the token has been
   revoked or expired.
3. Re-run the Plaid Link update-mode flow to obtain a new token.
4. Update the env var with the new token value.
5. Re-run `ledger doctor --production-preflight`, then `ledger sync`.

### 9.2 Webhook signature mismatch

**Symptoms:** `POST /webhooks/plaid` returns HTTP 400; logs show
"invalid Plaid webhook signature".

**Triage:**

1. Confirm `PLAID_WEBHOOK_SECRET` matches the signing secret in the
   Plaid dashboard (Webhooks → Signing secret).
2. If the secret was recently rotated in the dashboard, update the env
   var and restart `ledger serve`.
3. Verify the server is receiving the full raw request body unmodified
   (no middleware should alter the body before signature verification).

### 9.3 Stale cursor concerns

**Symptoms:** `doctor` shows a very old `last_synced_at`; transactions
from recent weeks are missing.

**Triage:**

1. Check that `ledger sync` (or `ledger sync --all`) is running on a
   regular schedule.
2. Run a manual sync:
   ```bash
   ledger sync
   ```
3. If sync succeeds but returns `added=0 modified=0 removed=0`, Plaid
   considers the cursor current — there are genuinely no new
   transactions.  Plaid's cursor is persistent; re-syncing from the same
   cursor is safe and idempotent.
4. If sync returns an error about an invalid cursor, delete the
   `sync_state` row for the affected item and re-sync from scratch:
   ```bash
   sqlite3 "$CLAW_PLAID_LEDGER_DB_PATH" \
       "DELETE FROM sync_state WHERE item_id = 'your-item-id';"
   ledger sync
   ```
   This will re-download all available transactions from Plaid's history
   window (typically 24 months).

### 9.4 Accidental wrong-environment configuration

**Symptoms:** `ledger doctor --production-preflight` shows
`[WARN] PLAID_ENV=sandbox`; live sync returns fake Plaid data.

**Triage:**

1. Verify `PLAID_ENV=production` in your config:
   ```bash
   grep PLAID_ENV ~/.config/claw-plaid-ledger/.env
   ```
2. Ensure no shell profile or `.env` file is overriding it with
   `sandbox`.
3. Confirm you are using the **production** `PLAID_CLIENT_ID` and
   `PLAID_SECRET` (sandbox and production keys are different).
4. Re-run `ledger doctor --production-preflight` — the sandbox warning
   should disappear.
5. If sandbox data was written to your production DB, the safest
   recovery is to restore from a backup taken before the contamination,
   then re-sync with correct production credentials.


### 9.5 Tracing a request end-to-end with `request_id` and `sync_run_id`

**Use case:** You need to follow one API request through webhook handling and
background sync logs.

1. Capture the request ID from the API response header:

```bash
curl -i -H "Authorization: Bearer $CLAW_API_SECRET" \
  http://127.0.0.1:8000/transactions?limit=1 | rg -i "x-request-id"
```

2. Trace that request through server logs:

```bash
rg "req-[a-f0-9]{8}" ~/.local/state/claw-plaid-ledger/server.log
```

3. If the request triggered a sync (for example from `POST /webhooks/plaid`),
   follow the corresponding sync correlation lines:

```bash
rg "sync-[a-f0-9]{8}" ~/.local/state/claw-plaid-ledger/server.log
```

Tip: webhook-triggered sync logs include linkage information between the
request correlation and sync correlation so operators can jump from `req-*` to
`sync-*` quickly during incident triage.

### 9.6 Webhook ingress security

> **Deprecated (M25).** This section applies only when webhooks are enabled
> via `CLAW_WEBHOOK_ENABLED=true`. Webhooks are disabled by default as of
> M25 in favor of the systemd sync timer (Section 12.3).

Plaid webhooks arrive at `POST /webhooks/plaid` from Plaid's published IP
ranges.  Three complementary enforcement layers are available:

#### Layer 1 — Application-layer IP allowlisting (`CLAW_WEBHOOK_ALLOWED_IPS`)

`ledger serve` can enforce Plaid's published source IP ranges directly in the
application, independent of your router or firewall.

Set `CLAW_WEBHOOK_ALLOWED_IPS` to a comma-separated list of IPv4/IPv6 CIDRs:

```bash
# In ~/.config/claw-plaid-ledger/.env
CLAW_WEBHOOK_ALLOWED_IPS="52.21.0.0/16,3.211.0.0/16"
```

When this variable is set, any `POST /webhooks/plaid` request whose resolved
source IP is not within one of the listed CIDRs receives HTTP 403
`{"detail": "forbidden"}` and a WARNING log line — before signature
verification even runs.

Plaid publishes its current webhook IP ranges in the Plaid developer
documentation (search "Plaid webhook IP ranges").  Review and update this list
when Plaid announces changes.

**Unset or empty** — no IP filtering is applied; existing behavior is
preserved.  `ledger doctor --production-preflight` will report a `[WARN]` for
this state to surface the choice explicitly (it is not a hard failure).

#### Layer 2 — `CLAW_TRUSTED_PROXIES` (required when behind a reverse proxy)

When `ledger serve` is behind Caddy, nginx, or another reverse proxy, the
direct connection IP seen by the application is always the proxy's loopback or
LAN address, not the real client IP.  Set `CLAW_TRUSTED_PROXIES` to the
proxy's IP(s) so the middleware reads `X-Forwarded-For` instead:

```bash
# Default (loopback proxy, the most common home-server setup):
CLAW_TRUSTED_PROXIES="127.0.0.1"

# Multiple trusted proxies (comma-separated):
CLAW_TRUSTED_PROXIES="127.0.0.1,10.0.0.1"
```

IP resolution order when the direct connection IP is in `CLAW_TRUSTED_PROXIES`:
1. Take the **leftmost** address from the `X-Forwarded-For` header as the real
   client IP.
2. If `X-Forwarded-For` is absent, fall back to the direct connection IP.

> **Security note:** Only list IPs you fully control as trusted proxies.
> A malicious client could inject a fake `X-Forwarded-For` header if the
> direct connection is not from a trusted proxy.

#### Layer 3 — Router / firewall rules (network-layer enforcement)

Complement the application-layer allowlist with router-level rules that drop
traffic to your webhook port from IP ranges that are not Plaid's:

```bash
# Example: ufw rule — replace <PORT> with your public webhook port
ufw allow from 52.21.0.0/16 to any port <PORT> proto tcp
ufw allow from 3.211.0.0/16 to any port <PORT> proto tcp
ufw deny to any port <PORT> proto tcp
```

Network-layer rules stop traffic before it reaches the application; they are
independent of and complementary to `CLAW_WEBHOOK_ALLOWED_IPS`.

#### Layer 4 — Plaid HMAC signature verification (cryptographic authenticity)

Even if an IP passes all network and application-layer filters, `ledger serve`
additionally verifies the Plaid-Verification HMAC-SHA256 signature on every
webhook body using `PLAID_WEBHOOK_SECRET`.  A request with a valid IP but a
tampered or missing signature receives HTTP 400.

**Recommended posture for production deployments:**

| Layer | Setting | Effect |
|-------|---------|--------|
| App IP allowlist | `CLAW_WEBHOOK_ALLOWED_IPS` set | Blocks non-Plaid IPs at app layer |
| Trusted proxy | `CLAW_TRUSTED_PROXIES` set | Correct IP resolution behind proxy |
| Firewall | Router rules | Blocks non-Plaid IPs at network layer |
| HMAC | `PLAID_WEBHOOK_SECRET` set | Cryptographic payload authenticity |

---

## 10. Stable webhook URL with DuckDNS

> **Deprecated (M25).** This section applies only to webhook-based sync,
> which is disabled by default as of M25. If you are using the recommended
> scheduled sync timer, DuckDNS and a public URL are not required. Webhook
> code remains available behind `CLAW_WEBHOOK_ENABLED=true`, but BUG-018
> is unresolved in multi-item setups.

### 10.1 Why a stable public URL is needed

Plaid requires a pre-registered webhook URL in the dashboard before it
will deliver events.  Home internet connections typically have a
dynamic public IP address that changes whenever the router reconnects,
which would break the registered URL.  DuckDNS provides a free dynamic
DNS service that maps a stable subdomain (`<subdomain>.duckdns.org`) to
your current public IP, so the URL you register with Plaid never
changes even when your IP does.

### 10.2 Account and subdomain registration

1. Visit [duckdns.org](https://www.duckdns.org) and sign in with a
   GitHub, Google, or Twitter account.
2. In the **domains** section, type a subdomain name of your choice
   and click **add domain**.  Use a random, unguessable subdomain
   (e.g. 8 lowercase alphanumeric characters) rather than anything
   descriptive — this makes your endpoint harder to discover by
   enumeration.  Generate one with:
   ```bash
   cat /dev/urandom | tr -dc 'a-z0-9' | head -c 8
   ```
3. Note the full hostname: `<subdomain>.duckdns.org`.

### 10.3 Finding your DuckDNS token

After signing in, your token is displayed at the top of the DuckDNS
dashboard page.  It looks like a UUID (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).
Keep it secret — it grants write access to all your DuckDNS subdomains.

### 10.4 Installing the update script and credentials

Install the script system-wide:

```bash
sudo cp scripts/duckdns-update.sh /usr/local/bin/duckdns-update.sh
sudo chmod 755 /usr/local/bin/duckdns-update.sh
```

Store credentials in a system config file.  The file must be readable
by the `caddy` group (see Section 10.7) and not world-readable:

```bash
sudo mkdir -p /etc/duckdns
sudo chmod 700 /etc/duckdns
sudo touch /etc/duckdns/credentials
sudo chmod 640 /etc/duckdns/credentials
sudo chown root:caddy /etc/duckdns/credentials
```

Edit `/etc/duckdns/credentials` and add:

```
DUCKDNS_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
DUCKDNS_DOMAIN=<subdomain>
```

For the systemd timer that runs the updates automatically, see
**Section 12.4**.

### 10.5 Pointing Plaid to your webhook URL

Plaid requires HTTPS with a valid certificate but does not restrict
which port the webhook URL uses.  Register the following URL in the
Plaid dashboard (Developers → Webhooks → Add webhook URL):

```
https://<subdomain>.duckdns.org:8443/webhooks/plaid
```

Using a nonstandard port (8443 recommended) adds a layer of obscurity
and keeps port 443 free for other services on the same host.

### 10.5.1 Registering the webhook URL on existing items

Adding a URL to the Plaid dashboard does **not** backfill items that were
linked before the webhook was configured.  Each existing item must be updated
explicitly with `ledger webhook-set`:

```bash
# Update all items in items.toml at once
ledger webhook-set --url https://<subdomain>.duckdns.org:8443/webhooks/plaid --all

# Single-item mode (uses PLAID_ACCESS_TOKEN)
ledger webhook-set --url https://<subdomain>.duckdns.org:8443/webhooks/plaid
```

Until this is run, Plaid will show no webhook deliveries for those items in
the dashboard even though the server is reachable and correctly configured.
Run `ledger webhook-set --all` once after completing Section 10.5, then verify
deliveries appear in the Plaid dashboard.

### 10.6 Router port-forward requirements

`ledger serve` listens on plain HTTP internally (default port 8000,
configurable via `CLAW_SERVER_PORT`).  A reverse proxy handles TLS
termination.  Only one inbound port forward is required:

- **TCP 8443** → this host, port 8443 (Caddy listens here and proxies
  to `ledger serve`)

Do **not** forward port 80 or 443.  Certificate issuance is handled
via DNS-01 challenge (see Section 10.7), which requires no inbound
port at all.

### 10.7 TLS termination with Caddy and DNS-01 (recommended)

The standard Caddy package does not include the DuckDNS DNS provider.
Build a custom binary with `xcaddy`:

```bash
sudo apt install golang-go
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
~/go/bin/xcaddy build --with github.com/caddy-dns/duckdns
sudo mv /usr/bin/caddy /usr/bin/caddy.stock
sudo mv ./caddy /usr/bin/caddy
sudo chmod 755 /usr/bin/caddy
```

Add a drop-in override so the Caddy service can read the DuckDNS
token from `/etc/duckdns/credentials`:

```bash
sudo mkdir -p /etc/systemd/system/caddy.service.d
```

Edit `/etc/systemd/system/caddy.service.d/override.conf`:

```ini
[Service]
EnvironmentFile=/etc/duckdns/credentials
```

Then reload systemd: `sudo systemctl daemon-reload`

Add this site block to `/etc/caddy/Caddyfile`:

```
<subdomain>.duckdns.org:8443 {
    tls {
        dns duckdns {env.DUCKDNS_TOKEN}
    }
    reverse_proxy localhost:8000
}
```

Caddy will obtain and renew the Let's Encrypt certificate automatically
via DNS-01 challenge — no inbound ports 80 or 443 required.

### 10.8 Testing the webhook URL before registering with Plaid

Verify the full HTTPS path is reachable before entering it in the
dashboard:

```bash
curl -v https://<subdomain>.duckdns.org:8443/health
```

Expected response: `{"status": "ok"}` with HTTP 200.  If this fails,
check your port-forward, TLS configuration, and that `ledger serve` is
running.

---

## 11. Scheduled sync fallback

> **Deprecated (M25).** The in-process fallback loop is superseded by the
> systemd timer (`ledger sync --all --notify`), which is now the primary
> sync mechanism. The fallback loop remains functional for backward
> compatibility but is no longer the recommended approach. See
> Section 12.3 for the recommended setup.

The scheduled sync fallback is an optional in-process background loop that
automatically triggers a sync for any configured item that has not been
synced within a configurable window (default 24 hours).

Enable it by setting `CLAW_SCHEDULED_SYNC_ENABLED=true` in your `.env`
before starting `ledger serve`.  The fallback window is controlled by
`CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` (default `24`; minimum `1`).

Run `ledger doctor` to confirm the current state:

```
scheduled-sync: ENABLED — fallback window 24h, check interval 60min
```

The loop is cancelled cleanly on server shutdown; no data is lost if
the server restarts mid-check.

---

## 12. Systemd deployment

This section covers running `ledger serve` as a managed systemd service
and scheduling syncs and DuckDNS updates via systemd timers.  The unit
files live in `deploy/systemd/` in the repository.

### 12.1 Prerequisites

- A Debian, Ubuntu, or Proxmox LXC host running systemd (version ≥ 240).
- `ledger` installed and reachable at a known absolute path
  (e.g. `/usr/local/bin/ledger` for system-wide installs,
  `/home/alice/.local/bin/ledger` for pipx/uv tool installs).
- The config directory and `.env` file created and permissions set:

  ```bash
  mkdir -p ~/.config/claw-plaid-ledger
  chmod 700 ~/.config/claw-plaid-ledger
  cp .env.example ~/.config/claw-plaid-ledger/.env
  chmod 600 ~/.config/claw-plaid-ledger/.env
  ```

- The data and log directories created:

  ```bash
  mkdir -p ~/.local/share/claw-plaid-ledger \
           ~/.local/state/claw-plaid-ledger
  ```

### 12.2 Installing the main service unit

Copy the unit file to the systemd system directory and reload the daemon:

```bash
sudo cp deploy/systemd/claw-plaid-ledger.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Before enabling, edit the file to match your environment:

| Placeholder | Replace with |
|---|---|
| `alice` (User/Group) | Your OS username |
| `/home/alice/...` paths | Actual absolute paths for config, data, and state dirs |
| `/usr/local/bin/ledger` | Actual path to the installed `ledger` binary |

Enable and start the service:

```bash
sudo systemctl enable --now claw-plaid-ledger
```

Confirm it is running:

```bash
sudo systemctl status claw-plaid-ledger
```

Expected output includes `Active: active (running)`.

### 12.3 Installing the scheduled-sync timer

The sync timer is the **primary sync mechanism** as of M25. It runs
`ledger sync --all --notify` four times daily (midnight, 06:00, noon,
18:00) and notifies the OpenClaw agent (Hestia) after each item that
produces changes.

To sync more frequently, create a drop-in override:

```bash
sudo systemctl edit claw-plaid-ledger-sync.timer
```

and add:

```ini
[Timer]
OnCalendar=
OnCalendar=hourly
```

The first empty `OnCalendar=` clears the default before setting hourly.

The in-process `CLAW_SCHEDULED_SYNC_ENABLED` fallback loop is deprecated
but remains functional for backward compatibility. Webhooks are disabled
by default (`CLAW_WEBHOOK_ENABLED=false`). Neither needs to be enabled
when using the systemd timer.

Install the service and timer:

```bash
sudo cp deploy/systemd/claw-plaid-ledger-sync.service /etc/systemd/system/
sudo cp deploy/systemd/claw-plaid-ledger-sync.timer   /etc/systemd/system/
sudo systemctl daemon-reload
```

Edit `claw-plaid-ledger-sync.service` to set the correct `User`,
`Group`, `EnvironmentFile`, and `ExecStart` paths (same adjustments as
for the main service unit).

Enable the timer (not the service directly — the timer activates it):

```bash
sudo systemctl enable --now claw-plaid-ledger-sync.timer
```

Verify the timer is scheduled:

```bash
systemctl list-timers claw-plaid-ledger-sync.timer
```

### 12.4 Installing the DuckDNS timer

> **Deprecated (M25).** The DuckDNS timer is only needed for webhook-based
> sync, which is disabled by default as of M25. Skip this section if you
> are using the recommended systemd sync timer (Section 12.3).

The DuckDNS update script and credentials are installed system-wide
(see Section 10.4).  Create the following unit files:

`/etc/systemd/system/duckdns.service`:

```ini
[Unit]
Description=DuckDNS dynamic DNS updater
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/duckdns/credentials
ExecStart=/usr/local/bin/duckdns-update.sh
StandardOutput=append:/var/log/duckdns.log
StandardError=append:/var/log/duckdns.log
```

`/etc/systemd/system/duckdns.timer`:

```ini
[Unit]
Description=DuckDNS update every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now duckdns.timer
```

Trigger the first run immediately and confirm it succeeded:

```bash
sudo systemctl start duckdns.service
cat /var/log/duckdns.log
# Expected: duckdns-update: <subdomain> → OK
```

### 12.5 Passing secrets securely via EnvironmentFile

The `EnvironmentFile=` directive loads `KEY=VALUE` pairs into the
service's environment before the process starts.  Critical rules:

- The file must be owned by the service user and mode **600** (not
  world-readable):

  ```bash
  chmod 600 ~/.config/claw-plaid-ledger/.env
  ls -l ~/.config/claw-plaid-ledger/.env
  # -rw------- 1 alice alice ...
  ```

- Never store secrets in the unit file itself — unit files in
  `/etc/systemd/system/` are world-readable by default.
- If multiple services share the same `.env`, verify that all
  co-located services are equally trusted.

### 12.6 Daily operations — status, logs, and restart

Check service status:

```bash
sudo systemctl status claw-plaid-ledger
sudo systemctl status claw-plaid-ledger-sync.timer
sudo systemctl status claw-plaid-ledger-duckdns.timer
```

Stream live logs (follow mode):

```bash
journalctl -u claw-plaid-ledger -f
```

View the last 100 lines for a unit:

```bash
journalctl -u claw-plaid-ledger -n 100 --no-pager
```

Restart the main service (e.g. after updating the `.env` file):

```bash
sudo systemctl restart claw-plaid-ledger
```

Stop and disable the service:

```bash
sudo systemctl disable --now claw-plaid-ledger
```

### 12.7 Drop-in overrides for site-specific customisation

To override individual directives without editing the shipped unit file,
use `systemctl edit` to create a drop-in snippet:

```bash
sudo systemctl edit claw-plaid-ledger
```

This opens an editor for `/etc/systemd/system/claw-plaid-ledger.service.d/override.conf`.
Changes here survive package updates that overwrite the base unit file.

Example drop-in: change the restart delay and add an extra environment
variable:

```ini
[Service]
RestartSec=30
Environment=CLAW_SERVER_PORT=9000
```

After editing, reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart claw-plaid-ledger
```

### 12.8 Proxmox LXC privilege considerations

When running inside a Proxmox LXC container:

- **Unprivileged containers** (recommended): systemd works normally.
  The unit files can be copied and enabled as described above.  The
  container's UID namespace means `User=alice` inside the container
  maps to a high UID on the Proxmox host — this is the intended,
  secure configuration.
- **Privileged containers**: systemd also works, but the host UID
  namespace is shared.  Apply the same file-permission hardening
  (`chmod 600` on `.env`, `chmod 700` on the config directory).
- The `ProtectSystem=strict` and `PrivateTmp=true` directives in the
  unit files are supported in both container types on recent Proxmox
  versions (PVE 7+).  If you see errors about missing kernel features,
  remove or comment out those lines and file a bug report.
- Bind-mounts for the config directory from host to container are
  supported; see the Proxmox documentation for `mp0` bind-mount
  configuration.

### 12.9 Deploying local changes to the installed binary

When developing from the repository and running the service via `uv tool
install`, use `scripts/deploy-local.sh` to reinstall the binary from the
local source tree and restart the service in one step:

```bash
bash scripts/deploy-local.sh
```

The script:

1. Runs `uv tool install --reinstall <repo-root>` to rebuild and overwrite
   the `ledger` binary at `~/.local/bin/ledger` from the current working
   tree.  The `--reinstall` flag forces a rebuild even when the version
   number has not changed, which is the normal state during local
   development.
2. Runs `sudo systemctl restart claw-plaid-ledger`.
3. Prints `systemctl status` output to confirm the service came back up.

> **Note:** This script requires `sudo` for the `systemctl restart` step.
> If your service is installed as a user unit (`systemctl --user`), remove
> the `sudo` from the script and adjust the `systemctl` calls accordingly.

---

## 13. Container deployment

This section covers two container approaches: Docker (the primary path) and
Proxmox LXC (for operators who prefer OS-level containers).

### 13.1 Docker — overview

The `deploy/docker/` directory contains a production-appropriate Docker image
definition and a Compose file.  Key design decisions:

- **Multi-stage build**: a `builder` stage (based on
  `ghcr.io/astral-sh/uv`) installs dependencies; the `runtime` stage is a
  slim Python 3.12 image containing only the installed virtualenv — no build
  tools, no source tree.
- **Non-root user**: the container runs as `ledger` (UID 1000).
- **No secrets in the image**: all configuration is supplied via environment
  variables or an `env_file` at run-time.
- **Loopback-only port binding**: port 8000 is bound to `127.0.0.1` by
  default so the container is not reachable from the network without a
  reverse proxy.

### 13.2 Docker — prerequisites

- Docker 24+ with the Compose plugin:

  ```bash
  docker compose version
  ```

- A valid `.env` file in `deploy/docker/` (never committed to version
  control).
- `items.toml` on the host at `~/.config/claw-plaid-ledger/items.toml`.

### 13.3 Docker — first-time setup

**Step 1 — Create the `.env` file.**

```bash
cat > deploy/docker/.env <<'EOF'
PLAID_CLIENT_ID=your-client-id
PLAID_SECRET=your-secret
PLAID_ENV=production
CLAW_API_SECRET=choose-a-strong-random-string
CLAW_DB_PATH=/data/ledger.db
# Optional: path to items.toml inside the container (default shown)
CLAW_ITEMS_CONFIG=/home/ledger/.config/claw-plaid-ledger/items.toml
EOF
chmod 600 deploy/docker/.env
```

The `.env` file must have mode 600 so only the owning user can read it.

**Step 2 — Prepare `items.toml`.**

```bash
mkdir -p ~/.config/claw-plaid-ledger
cp items.toml.example ~/.config/claw-plaid-ledger/items.toml
# Edit the file to add your Plaid items.
```

**Step 3 — Start the service.**

```bash
cd deploy/docker
docker compose up -d
```

Verify the service is running:

```bash
curl http://127.0.0.1:8000/health
# → {"status": "ok"}
```

### 13.4 Docker — build and update workflow

**Build the image locally** (required before first run or after code changes):

```bash
cd deploy/docker
docker compose build
```

Force a clean rebuild (useful after dependency updates):

```bash
docker compose build --no-cache
```

**Apply an update** (rebuild and restart with zero downtime of the volume):

```bash
docker compose build --no-cache
docker compose up -d
```

**View logs:**

```bash
docker compose logs -f ledger
```

**Restart the container** (e.g. after editing `.env`):

```bash
docker compose restart ledger
```

### 13.5 Docker — secrets management

- Store secrets in `deploy/docker/.env` with `chmod 600`.
- Never add `.env` to version control — the `.dockerignore` file already
  excludes it from the build context.
- For more isolation, use Docker secrets (`docker secret create`) or a
  secrets manager.  The application reads all config from environment
  variables, so any injection mechanism is compatible.
- Do not bake tokens into the image via `--build-arg`; build arguments are
  visible in `docker history`.

### 13.6 Docker — database backup and restore

The SQLite database lives in the `ledger-data` named volume.  Docker manages
the storage location; find it with:

```bash
docker volume inspect ledger-data
```

**Back up** the database to the current directory:

```bash
docker run --rm \
  -v ledger-data:/data \
  -v "$(pwd)":/backup \
  python:3.12-slim \
  cp /data/ledger.db /backup/ledger.db.bak
```

**Restore** from a backup:

```bash
# Stop the service first.
docker compose down
docker run --rm \
  -v ledger-data:/data \
  -v "$(pwd)":/backup \
  python:3.12-slim \
  cp /backup/ledger.db.bak /data/ledger.db
docker compose up -d
```

**Remove the volume** (destructive — deletes all transaction data):

```bash
docker compose down -v
```

### 13.7 LXC (Proxmox) — overview

Proxmox LXC provides OS-level container isolation without the overhead of a
full VM.  For `claw-plaid-ledger`, LXC is most useful when the operator
wants to run the service as a managed systemd unit inside its own container,
using the host's Proxmox scheduler.

Recommended approach: use the systemd unit files from `deploy/systemd/` inside
the LXC container (see Section 12), rather than running Docker-inside-LXC.

### 13.8 LXC — recommended setup

**OS template**: Debian 12 (bookworm) — the same distribution targeted by
the systemd unit files.

```
pct create 100 local:vztmpl/debian-12-standard_*.tar.zst \
  --hostname claw-plaid-ledger \
  --memory 512 \
  --cores 1 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --features nesting=1
```

**Bind-mount the config directory** from the Proxmox host into the container
so secrets are stored on the host and not inside the container filesystem:

```bash
# In /etc/pve/lxc/100.conf on the Proxmox host:
mp0: /host/path/claw-plaid-ledger-config,mp=/root/.config/claw-plaid-ledger
```

Replace `/host/path/claw-plaid-ledger-config` with the host path where you
store `items.toml` and `.env`.  Set `chmod 700` on that directory.

**Install `uv` and the service** inside the container:

```bash
pct enter 100
curl -LsSf https://astral.sh/uv/install.sh | sh
# Follow the steps in RUNBOOK.md Section 12 to install the unit files.
```

### 13.9 LXC — systemd vs host-level service management

Two patterns are available:

| Pattern | How it works | When to use |
|---|---|---|
| **systemd-inside-LXC** | The LXC container runs its own init (systemd); `ledger serve` is a unit inside the container | Preferred — clean isolation, standard `systemctl` and `journalctl` workflow |
| **Host-level via pct exec** | Proxmox host manages the process via a `.service` unit that calls `pct exec` | Useful if the LXC container is a minimal image without systemd |

For most Proxmox home-server setups the first pattern is simpler.  Enable
it by creating the container with `--features nesting=1` (already shown
above) and following Section 12 inside the container.

### 13.10 LXC — privilege considerations

- **Unprivileged containers** (recommended): UIDs inside the container are
  mapped to high host UIDs.  File permissions on bind-mounted config
  directories must account for this mapping (use `chown 100000:100000` on
  the host path if the container user is UID 0, or match the mapped UID for
  non-root users).
- **Privileged containers**: UIDs are shared with the host.  Avoid unless
  required; a privilege-escalation bug in the container could affect the
  host.
- The `ProtectSystem=strict` and `PrivateTmp=true` systemd directives work
  in both modes on Proxmox PVE 7+.

---

## 14. Auth hardening — reverse-proxy patterns

This section explains how to add a network-layer authentication boundary in
front of `ledger serve` using a reverse proxy.  The two primary patterns are:

| Pattern | Description |
|---|---|
| **mTLS (client certificates)** | Agents and operators must present a certificate signed by a trusted CA to access protected routes |
| **OIDC / SSO (Authelia)** | Browser and interactive access is gated behind a login page with optional MFA |

Both patterns are **additive** — the `CLAW_API_SECRET` bearer token is always
required for API calls.  The reverse proxy guards the network boundary; the
application guards the API boundary.

See `deploy/proxy/` for ready-to-use configuration examples:

```
deploy/proxy/
  Caddyfile.example         Caddy v2 mTLS configuration
  nginx-mtls.conf.example   nginx equivalent
  authelia-notes.md         Authelia OIDC/SSO integration guide
```

---

### 14.1 Decision guide

**Use mTLS when:**
- API callers are scripts, OpenClaw agents, or automation tools — not humans
  using a browser.
- You want cert-per-agent identity that works with any HTTP client.
- You have a home LAN or VPN where you control all clients.
- You want zero external IdP dependency (self-signed CA).

**Use Authelia (OIDC) when:**
- Humans need browser-based access (Swagger UI, ad-hoc `curl`).
- A household shares the service and you want per-user accounts and audit logs.
- You want MFA (TOTP / WebAuthn) for interactive logins.

**Use both when:**
- Agents use mTLS for programmatic access; operators use Authelia for browser
  sessions.  The two patterns do not conflict.

---

### 14.2 mTLS walkthrough — generating a self-signed CA and client cert

This walkthrough uses `openssl`.  Run these commands on a trusted workstation,
not on the server.

#### Step 1 — Generate a CA key and self-signed certificate

```bash
# CA private key (keep this secret — anyone with it can issue trusted certs)
openssl genrsa -out ca.key 4096

# Self-signed CA certificate, valid for 10 years
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=claw-plaid-ledger-CA/O=home"
```

#### Step 2 — Generate a client certificate (one per agent or operator)

```bash
# Client private key
openssl genrsa -out client.key 2048

# Certificate signing request
openssl req -new -key client.key -out client.csr \
  -subj "/CN=ledger-agent/O=home"

# Sign the CSR with your CA — valid for 1 year
openssl x509 -req -days 365 -in client.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt
```

#### Step 3 — Distribute files

| File | Where it goes |
|---|---|
| `ca.crt` | Server — Caddy or nginx trust root (`/etc/caddy/certs/ca.crt` or `/etc/nginx/certs/ca.crt`) |
| `server.crt` + `server.key` | Server — the TLS certificate nginx/Caddy presents to clients |
| `client.crt` + `client.key` | Agent workstation or agent runtime — presented during TLS handshake |

Set restrictive permissions on key files:

```bash
chmod 600 ca.key client.key server.key
```

#### Step 4 — Configure Caddy or nginx

Follow `deploy/proxy/Caddyfile.example` or `deploy/proxy/nginx-mtls.conf.example`.
Both files contain inline comments that map directly to the files generated above.

#### Step 5 — Set CLAW_TRUSTED_PROXIES

Add the proxy host IP to your `.env` so webhook IP allowlisting (Section 9.6)
resolves the real Plaid source IP:

```
# When Caddy/nginx runs on the same host as ledger serve:
CLAW_TRUSTED_PROXIES=127.0.0.1

# When Caddy/nginx runs on a different host (replace with actual IP):
CLAW_TRUSTED_PROXIES=10.0.0.1
```

#### Step 6 — Test

```bash
# Health check — no client cert needed:
curl https://ledger.home.example/health

# Protected route without cert — should return 403 or TLS handshake error:
curl https://ledger.home.example/transactions \
  -H "Authorization: Bearer <CLAW_API_SECRET>"

# Protected route with cert — should return 200:
curl https://ledger.home.example/transactions \
  --cert client.crt --key client.key \
  -H "Authorization: Bearer <CLAW_API_SECRET>"
```

---

### 14.3 Configuring Caddy mTLS

Copy `deploy/proxy/Caddyfile.example` to your Caddy configuration directory
and adjust the following values:

| Placeholder | Replace with |
|---|---|
| `ledger.home.example` | Your server hostname or DuckDNS FQDN |
| `/etc/caddy/certs/server.crt` | Path to your server TLS certificate |
| `/etc/caddy/certs/server.key` | Path to your server TLS private key |
| `/etc/caddy/certs/ca.crt` | Path to your CA certificate (clients must be signed by this) |
| `127.0.0.1:8000` | Upstream `ledger serve` address (default is correct for same-host) |

After editing:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

For **Let's Encrypt** (public FQDN), uncomment Option B in the example file.
Caddy manages the server cert automatically; you still supply the CA cert for
client verification.

---

### 14.4 Cert rotation

Client certificates should be rotated periodically (annually is common for
home setups).  The rotation procedure avoids downtime:

1. Generate a new client cert using the same CA (`Step 2` above — no CA
   change needed).
2. Distribute the new `client.crt` + `client.key` to the agent or operator.
3. The old cert continues to work until you revoke it.
4. Test the new cert against the protected endpoints.
5. Remove or archive the old cert from agent runtimes.

**CA rotation** (less frequent, e.g. when the CA key is compromised):

1. Generate a new CA key and certificate.
2. Issue new client certs signed by the new CA.
3. On the server, replace the `trusted_ca_certs_pem_files` / `ssl_client_certificate`
   path with a combined PEM that includes **both** the old and new CA certs.
   This allows old and new client certs to work simultaneously during the
   transition window.
4. Distribute new client certs.
5. After all agents are updated, remove the old CA from the combined PEM and
   reload Caddy/nginx.

Combined CA PEM:

```bash
cat old-ca.crt new-ca.crt > combined-ca.crt
# Point Caddyfile / nginx config at combined-ca.crt during transition.
```

---

### 14.5 Authelia OIDC front-proxy

See `deploy/proxy/authelia-notes.md` for a complete integration guide covering:

- When to choose Authelia vs mTLS.
- Minimal `configuration.yml` stubs for access control rules.
- Caddy `forward_auth` and nginx `auth_request` integration snippets.
- How `CLAW_API_SECRET` and Authelia layers stack.

---

### 14.6 Security reminder

The reverse-proxy layer and `CLAW_API_SECRET` are independent and complementary:

| Layer | Enforces | Where configured |
|---|---|---|
| Network — reverse proxy | Who can reach the service | Caddy / nginx / Authelia |
| Application — bearer token | Who can call the API | `CLAW_API_SECRET` in `.env` |

**Never remove `CLAW_API_SECRET`** even when running behind mTLS or Authelia.
The bearer token ensures that an attacker who bypasses the proxy layer (e.g.
via a misconfigured firewall rule) cannot read financial data without the token.

---

## Section 15 — Deployment selection guide

Use this section to choose the right deployment strategy for your setup.
Every path ends at the same running `ledger serve` process — the difference
is how it is managed and what network boundary sits in front of it.

### 15.1 Deployment method decision table

| Scenario | Recommended method | Go to |
|---|---|---|
| **Development / local testing** | Bare `ledger serve` | — |
| **Linux home server (Debian/Ubuntu/Proxmox host)** | systemd service | Section 12 |
| **Containerized setup (any OS)** | Docker Compose | Section 13 |
| **Proxmox OS container (LXC)** | LXC + systemd-inside-LXC | Section 13.2 |
| **NAS / low-power appliance (non-systemd)** | Docker Compose | Section 13 |

#### Bare `ledger serve` (dev/test only)

Run directly from the terminal for development, troubleshooting, or a
one-off sync-and-serve session:

```bash
source ~/.config/claw-plaid-ledger/.env
ledger serve
```

This is **not suitable for production** because there is no automatic restart
on failure and no OS-level process supervision.

#### systemd (Linux/Proxmox — recommended for production)

Best for operators who want the OS to manage the process lifecycle:

- Automatic start at boot via `WantedBy=multi-user.target`
- Automatic restart on failure (`Restart=on-failure`)
- Structured logs via `journalctl -u claw-plaid-ledger`
- Scheduled sync and DuckDNS timer units included

→ See **Section 12** for the complete install and enable walkthrough.

#### Docker Compose (containerized setup)

Best for operators who prefer container isolation or run multiple services
on a single host using Docker:

- Application runs as a non-root user (`ledger`, UID 1000)
- Secrets passed via `env_file`, never baked into the image
- Named volume for the SQLite database preserves data across container restarts
- Single command to start: `docker compose up -d`

→ See **Section 13** for build, run, and update instructions.

#### LXC (Proxmox OS container)

Best for Proxmox operators who want OS-level isolation without the overhead
of a full VM:

- Use the Debian LXC template
- Mount the config directory from the host using a bind-mount
- Run `ledger serve` under systemd inside the container (same as Section 12)
- Or run `docker compose` inside the LXC if Docker isolation is preferred

→ See **Section 13.2** for Proxmox-specific LXC guidance.

---

### 15.2 Auth hardening decision table

Choose the network-layer auth boundary based on your access model.
`CLAW_API_SECRET` is always required regardless of which option you pick.

| Access model | Recommended pattern | Go to |
|---|---|---|
| **Simple home LAN, single operator, no browser UI access** | No proxy (direct `127.0.0.1` bind) | — |
| **Automated agents calling the API over LAN or Tailscale** | Caddy mTLS | Section 14 |
| **nginx already running on the host** | nginx mTLS | Section 14.3 |
| **Browser-based access or shared household (multiple users)** | Authelia OIDC front-proxy | Section 14.5 |
| **Reverse proxy present but no client-cert requirement** | Bearer token only (`CLAW_API_SECRET`) | — |

#### No proxy (simple home LAN)

`ledger serve` binds to `127.0.0.1:8000` by default.  Agents running on the
same host reach it directly.  If agents run on other LAN hosts, bind to the
host's LAN IP and ensure the host firewall restricts access to trusted hosts.

`CLAW_API_SECRET` remains the sole auth mechanism.  This is adequate for a
single-operator home setup where the LAN is trusted.

No additional configuration is required.

#### Caddy mTLS (agent access hardening)

Adds a client-certificate requirement in front of the API.  Only clients
presenting a certificate signed by your local CA can reach the protected
routes.  Best for automated agents where a certificate-per-agent model is
preferred over a shared bearer token.

→ See **Section 14** for the CA generation, cert issuance, Caddy
configuration, and cert rotation walkthrough.

→ Copy `deploy/proxy/Caddyfile.example` as your starting point.

#### Authelia OIDC front-proxy (browser + shared access)

Adds SSO-style authentication in front of the Swagger UI and API.  Best
when multiple household members access the system interactively via a browser
or when per-user audit logs are required.

→ See **Section 14.5** for the Authelia integration guide.

→ Read `deploy/proxy/authelia-notes.md` for complete configuration stubs.

---

### 15.3 Combining deployment method and auth pattern

The deployment method and auth pattern are independent choices.  Common
combinations:

| Deployment | Auth | Notes |
|---|---|---|
| systemd | No proxy | Standard single-operator home setup |
| systemd | Caddy mTLS | Add Caddy as a second systemd service on the same host |
| Docker Compose | No proxy | Expose port to `127.0.0.1` only (default) |
| Docker Compose | Caddy mTLS | Add Caddy container to the Compose stack or run on host |
| LXC + systemd | Authelia OIDC | Proxmox multi-user household with browser access |

When a reverse proxy is in use, always set `CLAW_TRUSTED_PROXIES` to the
proxy's IP so that `X-Forwarded-For` headers are resolved correctly by the
webhook IP allowlist.  See Section 9.6 for allowlist configuration details.

---

### 15.4 Quick reference: section cross-index

| Topic | Section |
|---|---|
| systemd unit files — install and enable | Section 12 |
| systemd — drop-in overrides, `journalctl`, restart | Section 12 |
| Docker — build, run, and update | Section 13 |
| LXC (Proxmox) — OS container guidance | Section 13.2 |
| Webhook ingress IP allowlisting | Section 9.6 |
| Caddy mTLS — CA generation and cert issuance | Section 14 |
| nginx mTLS | Section 14.3 |
| Authelia OIDC front-proxy | Section 14.5 |
| Cert rotation | Section 14.4 |
| Production preflight checklist | Section 3 |
| DuckDNS stable webhook URL | Section 10 |
| Scheduled sync fallback | Section 11 |
| Skill registration (sync-skills push) | Section 16 |

---

## 16. Skill registration

### What `sync-skills.sh push` does end-to-end

Running `./scripts/sync-skills.sh push` performs two actions for each skill:

1. **Copies skill files** — uses `rsync` to mirror the skill directory from
   this repo into the agent's openclaw workspace:
   ```
   skills/<skill-name>/  →  ~/.openclaw/workspace/agents/<agent>/skills/<skill-name>/
   ```

2. **Updates the agent's `TOOLS.md`** — reads the skill's `SKILL.md`
   frontmatter (`name`, `description`, and
   `metadata.openclaw.requires.env`) and upserts a `## Skills` block in
   `~/.openclaw/workspace/agents/<agent>/TOOLS.md` between sentinel markers:
   ```
   <!-- sync-skills-start -->
   ...
   <!-- sync-skills-end -->
   ```
   Content outside the sentinel markers is never modified. The operation is
   **idempotent**: running push twice produces the same TOOLS.md.

### Verify the injection worked

After running push, inspect the target agent's TOOLS.md:

```bash
cat ~/.openclaw/workspace/agents/hestia/TOOLS.md
cat ~/.openclaw/workspace/agents/athena/TOOLS.md
```

Confirm that a `## Skills` section is present and contains the correct
`### <skill-name>` entry with matching description, skill path, and required
environment variable names.

### Re-run push after updating a skill definition

Push is safe to re-run at any time. After editing a skill's `SKILL.md`
frontmatter, simply run:

```bash
./scripts/sync-skills.sh push
```

The script will overwrite the existing entry between the sentinel markers
with the updated values. No manual TOOLS.md editing is required.

### Manual fallback

If `sync-skills.sh` is unavailable, copy the template below into the agent's
TOOLS.md and fill in the fields from the skill's `SKILL.md` frontmatter:

```markdown
## Skills (managed by sync-skills.sh — do not edit between markers)

<!-- sync-skills-start -->
### <name>
- **Description:** <description from SKILL.md>
- **Skill path:** ~/.openclaw/workspace/agents/<agent>/skills/<name>/SKILL.md
- **Required env:** `VAR1`, `VAR2`
<!-- sync-skills-end -->
```

- `name` — the `name:` field in the SKILL.md YAML frontmatter.
- `description` — the `description:` field.
- `VAR1`, `VAR2` — the entries under
  `metadata.openclaw.requires.env` in the SKILL.md frontmatter.

---

## 17. Account labels & enriched spend queries

### Labelling accounts

Once accounts have been synced (`ledger sync --all`), retrieve the list to
discover account IDs:

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  http://127.0.0.1:8000/accounts | jq .
```

Then attach a human-readable label to any account:

```bash
curl -s -X PUT \
  -H "Authorization: Bearer $CLAW_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"label": "Alice Joint Checking", "description": "Primary household account"}' \
  http://127.0.0.1:8000/accounts/acc_abc123 | jq .
```

The response is the full account record with the newly written fields.
Labels survive sync runs — the sync engine never writes to `account_labels`.

To clear a label, send `null`:

```bash
curl -s -X PUT \
  -H "Authorization: Bearer $CLAW_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"label": null, "description": null}' \
  http://127.0.0.1:8000/accounts/acc_abc123 | jq .
```

### Scoped spend queries

**By account:**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=this_month&account_id=acc_abc123" | jq .
```

**By allocation category (case-insensitive):**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=last_month&category=software" | jq .
```

**By multiple allocation categories (OR semantics across categories):**

```bash
# Sum allocations whose category is groceries OR dining.
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=last_month&category=groceries&category=dining" \
  | jq .
```

Repeated `?category=...` params filter per allocation row, so split
transactions contribute only the matching allocation amounts (not the full
transaction amount). NULL-category allocations are excluded.

**By allocation tag (case-insensitive, singular):**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=last_30_days&tag=recurring" | jq .
```

**Combined filters (AND semantics outside the category OR group):**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=this_month&account_id=acc_abc123&category=software&category=saas&tag=recurring" \
  | jq .
```

All filter keys (`owner`, `tags`, `account_id`, `category`, `categories`,
`tag`) are always present in the `filters` object of the response, even when
not supplied:

```json
{
  "filters": {
    "owner": null,
    "tags": [],
    "account_id": "acc_abc123",
    "category": "software",
    "categories": ["software"],
    "tag": null
  }
}
```

`filters.categories` always echoes the full requested list (empty when none
supplied). `filters.category` preserves the legacy scalar — populated only
when exactly one category was requested. Prefer `filters.categories` for new
clients.

### Updating skill bundles

If you need to re-push the skill bundles to your OpenClaw workspace:

```bash
./scripts/sync-skills.sh push
```

This copies the `hestia-ledger` and `athena-ledger` bundles into your
agent workspace directories and refreshes TOOLS.md. See Section 16 for
the full push walkthrough.

## 18. Month-over-month spend trends

`GET /spend/trends` returns spend aggregated by calendar month without
requiring multiple `GET /spend` calls and manual date arithmetic.

### Basic usage

Six-month lookback (default):

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends" | jq .
```

Custom lookback window:

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=3" | jq .
```

Response is a plain JSON array — one bucket per calendar month, oldest first:

```json
[
  {"month": "2026-01", "total_spend": 2980.00, "allocation_count": 41, "partial": false},
  {"month": "2026-02", "total_spend": 3100.25, "allocation_count": 44, "partial": false},
  {"month": "2026-03", "total_spend":  850.00, "allocation_count": 12, "partial": true}
]
```

The current calendar month always has `partial: true`; all prior complete
months have `partial: false`. Months with no qualifying transactions appear
as zero-filled buckets (`total_spend: 0.0`, `allocation_count: 0`) and are
never omitted.

### Scoped trends

**By owner:**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=6&owner=alice" | jq .
```

**By account:**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=6&account_id=acc_abc123" | jq .
```

**By allocation category (case-insensitive; repeatable):**

```bash
# Single category (legacy, identical to prior behavior).
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=6&category=software" | jq .

# Multiple categories — OR semantics across categories. Each monthly bucket
# sums allocation rows whose category is software OR saas.
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=6&category=software&category=saas" \
  | jq .
```

**By allocation tag (case-insensitive, singular):**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=6&tag=recurring" | jq .
```

**Multiple tags (AND semantics):**

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=6&tags=groceries&tags=household" | jq .
```

### Validating a specific month's total

Cross-check any bucket against `GET /spend` using matching filters:

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?start_date=2026-01-01&end_date=2026-01-31" | jq .
```

The `total_spend` values must agree between the two endpoints when the same
filters are applied.

### Notes

- `?months=0` or `?months=-1` returns HTTP 422 (minimum is 1).
- Supports `include_pending=true` and `view=raw` with identical semantics to
  `GET /spend`.
- The current month's bucket is always partial; avoid direct comparisons
  between it and prior complete months.

---

## 19. Ledger error log monitoring

`GET /errors` exposes recent ledger warnings and errors recorded automatically
during server operation. Any WARNING, ERROR, or CRITICAL log emitted by any
logger while `ledger serve` is running is persisted to the `ledger_errors`
table without per-call instrumentation. Rows older than 30 days are pruned
automatically on each insert.

### Checking recent errors

Default 24-hour window:

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/errors" | jq .
```

Last hour, errors only:

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/errors?hours=1&min_severity=ERROR" | jq .
```

Last week:

```bash
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/errors?hours=168" | jq .
```

Response shape:

```json
{
  "errors": [
    {
      "id": 1,
      "severity": "ERROR",
      "logger_name": "claw_plaid_ledger.server",
      "message": "background sync failed: connection refused",
      "correlation_id": "sync-a1b2c3d4",
      "created_at": "2026-03-22T10:05:00.000000+00:00"
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0,
  "since": "2026-03-21T10:05:00.000000+00:00"
}
```

- `total` is the full matching count before `limit`/`offset`.
- `since` is the UTC start of the lookback window.
- `correlation_id` links the error to a specific API request (`req-xxxxxxxx`)
  or sync run (`sync-xxxxxxxx`). Use it with `journalctl` or server logs to
  retrieve full context.

### `doctor` error-log summary

`ledger doctor` always reports a one-line error-log summary:

```
doctor: error-log warn=2 error=0 (last 24h)
```

This count covers the last 24 hours. If `error > 0`, check the server logs
and call `GET /errors?min_severity=ERROR` to retrieve the specific rows.
The `doctor` command does not exit non-zero based on these counts — they are
informational. Operators and agents can inspect the counts and decide whether
to investigate further.

### Pagination

`GET /errors` uses the same offset-based pagination as `GET /transactions`:

```bash
# Page 1
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/errors?limit=50&offset=0" | jq .

# Page 2
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/errors?limit=50&offset=50" | jq .
```

Stop when `offset >= total`.

### Tracing an error with its correlation ID

When an error row contains a `correlation_id`:

```bash
# Filter server logs by the request ID
journalctl -u claw-plaid-ledger --since "1 hour ago" | grep "req-a1b2c3d4"

# Or filter by a sync run ID
journalctl -u claw-plaid-ledger --since "1 hour ago" | grep "sync-a1b2c3d4"
```

See also Section 9.5 for the full request-tracing walkthrough.

### Notes

- `?hours=0` returns HTTP 422 (minimum lookback is 1 hour).
- `?limit` maximum is 500; larger values return HTTP 422.
- Rows are ordered newest first (`created_at DESC`).
- Error persistence covers all server-context code paths: webhook handlers,
  background sync, scheduled sync loop, and API request handlers.
- CLI sync commands (`ledger sync`, `ledger sync --all`) are intentionally out
  of scope — they are interactive and print to the terminal.

---

## 20. Allocation model and budgeting layer

Every transaction carries an `allocation` object in all API responses.
Allocations are the sole budgeting layer: spend totals, category/tag
vocabulary, and note search all read from `allocations`.

### How allocations are seeded

Every transaction automatically receives a blank allocation row at two points:

1. **On sync** — `upsert_transaction()` inserts a blank allocation
   (`amount = transaction.amount`, `category`/`tags`/`note` null) for any new
   transaction that does not already have one.
2. **On startup** — `initialize_database()` backfills an allocation for any
   transaction that has no allocation row yet. This handles transactions synced
   before M20 and is idempotent.

### Reading allocations

The detail endpoint (`GET /transactions/{id}`) returns an `allocations` array
(never null). For unsplit transactions it has one element; for split
transactions it has all allocations ordered by `id ASC`:

```json
{
  "id": "txn_abc123",
  "amount": 12.34,
  ...
  "allocations": [
    {
      "id": 1,
      "amount": 12.34,
      "category": "groceries",
      "tags": ["household"],
      "note": "weekly shopping",
      "updated_at": "2026-03-25T10:00:00+00:00"
    }
  ]
}
```

The list endpoint (`GET /transactions`) returns a singular `"allocation": {...}`
key per row (each row is one transaction–allocation pair).

Two queue-specialized list variants are also available:

- `GET /transactions/uncategorized` — same filter/pagination surface as
  `GET /transactions`, pre-filtered to rows where `allocation.category` is
  null.
- `GET /transactions/splits` — same filter/pagination surface as
  `GET /transactions`, pre-filtered to transactions with more than one
  allocation (returns all allocations for each split transaction).

`category`, `tags`, and `note` within each allocation element may be null for
uncategorized transactions.

### Writing allocations (primary path)

Use `PUT /transactions/{transaction_id}/allocations` — accepts a JSON array and
atomically replaces all existing allocations:

```bash
# Single-allocation (unsplit) write
curl -s -X PUT \
  -H "Authorization: Bearer $CLAW_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '[{"amount": 12.34, "category": "groceries", "tags": ["household"], "note": "weekly shopping"}]' \
  http://127.0.0.1:8000/transactions/txn_abc123/allocations | jq .

# Split transaction across two categories
curl -s -X PUT \
  -H "Authorization: Bearer $CLAW_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '[{"amount": 60.00, "category": "groceries"}, {"amount": 40.00, "category": "household"}]' \
  http://127.0.0.1:8000/transactions/txn_abc123/allocations | jq .
```

Amounts are auto-corrected if the sum differs from the transaction amount by
≤ $1.00 (last item silently adjusted). Returns HTTP 422 if the difference
exceeds $1.00. The response contains the full transaction detail with
`"allocations": [...]`.

### Writing allocations (batch path for unsplit transactions)

Use `POST /transactions/allocations/batch` when updating many
single-allocation transactions in one request:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $CLAW_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '[
    {"transaction_id": "txn_1", "category": "groceries", "tags": ["household"]},
    {"transaction_id": "txn_2", "category": "utilities", "note": "monthly bill"}
  ]' \
  http://127.0.0.1:8000/transactions/allocations/batch | jq .
```

Batch responses are always HTTP 200 and return `{succeeded, failed}`.
Processing is per-item (collect-all-errors semantics): one failed item does not
abort later items.

Important: batch updates use **replace semantics** for semantic fields. Omitted
`category`, `tags`, or `note` fields are cleared (`NULL`) on success.
Split transactions are rejected per item with guidance to use
`PUT /transactions/{id}/allocations`.

### Filtering spend by allocation category or tag

`GET /spend` and `GET /spend/trends` filter against allocation fields. The
`category` filter is repeatable on both endpoints — multiple values use OR
semantics across categories, and filtering happens per allocation row so
split transactions contribute only the matching allocation amounts:

```bash
# Spend for a specific allocation category this month
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=this_month&category=groceries" | jq .

# Spend across multiple categories (OR)
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=this_month&category=groceries&category=dining" \
  | jq .

# Trends filtered to a single allocation tag
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend/trends?months=6&tag=recurring" | jq .
```

The same repeated `?category=` parameter is accepted by `GET /transactions`
and `GET /transactions/splits`. `GET /transactions/uncategorized` rejects
named `category` filters (HTTP 422); use it as the dedicated workflow for
allocations where `category IS NULL`.

### Notes

- As of M21 (Sprint 23), multi-allocation (split) transactions are fully
  supported via `PUT /transactions/{id}/allocations`. A transaction split into
  N allocations produces N rows in `GET /transactions` list results.
- The `allocations` table has no UNIQUE constraint on `plaid_transaction_id`
  by design, allowing multiple allocation rows per transaction.
- `GET /categories` and `GET /tags` return vocabulary from `allocations`.
- `GET /spend` and `GET /spend/trends` use `allocation_count` (renamed from
  `transaction_count` in M21) to reflect allocation-row semantics.

---

## 21. Multi-allocation editing (split transactions)

M21 (Sprint 23) makes it possible to split a single imported transaction across
multiple categories.

### CLI: viewing and setting allocations

```bash
# View current allocation state for a transaction
ledger allocations show txn_abc123

# Replace allocations from a JSON file
ledger allocations set txn_abc123 --file allocations.json

# Replace allocations from stdin
echo '[{"amount": 100.00, "category": "groceries"}]' \
  | ledger allocations set txn_abc123 --file -
```

`ledger allocations show` output:

```
Transaction: txn_abc123
  Date:     2026-03-15
  Merchant: AMAZON.COM
  Amount:   $100.00

Allocations (2):
  #1   $60.00   groceries     [household]   food
  #2   $40.00   household     (no tags)     (no note)
  ──────────────────────────────────────────────
  Total: $100.00   ✓ Balanced
```

### API: `PUT /transactions/{id}/allocations`

See ARCHITECTURE.md for the full endpoint spec. Key validation rules:

- Empty array → HTTP 422.
- Sum differs from transaction amount by > $1.00 → HTTP 422 with
  `transaction_amount`, `allocation_total`, `difference` in the error body.
- Sum differs by ≤ $1.00 → last item silently adjusted; HTTP 200 returned.

### List pagination with split transactions

`GET /transactions` returns one row per allocation. A transaction split into
two categories appears as two rows (same `id`, different `allocation` objects).
The `total` field in the pagination response counts allocation rows, not
transaction rows, so `offset`/`limit` pagination works correctly.

### Spend rollup for split transactions

`GET /spend?category=groceries` sums only the grocery allocation amounts —
not the full transaction amount — so split-category filtering is always
accurate. Multiple categories use OR semantics across categories, still
allocation-row scoped:

```bash
# Sums only allocation rows whose category is groceries OR dining.
curl -s -H "Authorization: Bearer $CLAW_API_SECRET" \
  "http://127.0.0.1:8000/spend?range=last_month&category=groceries&category=dining" \
  | jq .
```

For the transaction list, the same semantics apply: a split transaction with
allocations in `groceries` and `dining` returns only the matching allocation
row(s) when `?category=...` is supplied. Unrelated allocation rows from the
same transaction never appear in filtered list results.

---

## 22. On-demand Plaid refresh (`ledger refresh`)

M22 (Sprint 24) adds a CLI command that tells Plaid to re-check an institution
and fire `SYNC_UPDATES_AVAILABLE` to the registered webhook URL. Use this to
confirm end-to-end webhook delivery in production without raw HTTP tooling.

### When to use it

- After configuring or rotating a webhook URL — confirm Plaid can reach the
  new URL before waiting for a natural sync event.
- During incident triage — rule out Plaid-side silence vs. a missed webhook
  by forcing a refresh and checking the webhook delivery log in the Plaid
  dashboard.
- After setting up a new item — trigger an immediate refresh to confirm the
  access token is valid and webhooks are flowing.

### Commands

```bash
# Single item (PLAID_ACCESS_TOKEN)
ledger refresh

# Single named item from items.toml
ledger refresh --item bank-alice

# All items in items.toml
ledger refresh --all
```

### Expected output

```
# Default mode (single item via PLAID_ACCESS_TOKEN)
refresh: OK

# --item mode
refresh[bank-alice]: OK

# --all mode
refresh[bank-alice]: OK
refresh[card-bob]: OK
refresh --all: 2 items refreshed, 0 failed
```

On failure:

```
refresh[bank-alice]: ERROR Plaid permanent API error (HTTP 400): ...
refresh --all: 1 items refreshed, 1 failed
```

### Exit codes

| Exit code | Meaning |
|---|---|
| 0 | All items refreshed successfully |
| 1 | One or more adapter errors (Plaid API or network) |
| 2 | Missing required config, missing token, or `--item`+`--all` together |

### Notes

- `ledger refresh` does **not** immediately sync transactions. It instructs
  Plaid to fire `SYNC_UPDATES_AVAILABLE`, which the server receives via
  `POST /webhooks/plaid` and processes in the background.
- The `/transactions/refresh` endpoint is available in both sandbox and
  production. In sandbox, Plaid simulates the webhook; in production, it
  triggers a real check against the institution.
- `--item` and `--all` are mutually exclusive; using both together exits 2.


---

## 23. Post-upgrade cleanup (after Sprint 26)

After deploying the `ledger-api` wrapper and pushing updated skill bundles,
clean up stale exec-approval and config entries.

### 23.1 Remove stale curl allowlist entry from `exec-approvals.json`

Open `~/.openclaw/exec-approvals.json` and remove the `/usr/bin/curl` object
from `agents.hestia.allowlist`. Also remove `/usr/bin/echo` if it was only
used for skill debugging. Keep `allowlist` as `[]` if no other entries remain.

```json
{
  "id": "5d35405b-...",
  "pattern": "/usr/bin/curl"
}
```

Restart the gateway after editing:

```bash
openclaw gateway restart
```

### 23.2 Optional: remove redundant `CLAW_LEDGER_URL` from `openclaw.json`

The `ledger-api` wrapper defaults `CLAW_LEDGER_URL` to
`http://127.0.0.1:8000`, so `skills.entries.*.env.CLAW_LEDGER_URL` is
redundant. You can simplify each entry to:

```json
"hestia-ledger": {
  "apiKey": { "source": "env", "provider": "default", "id": "CLAW_API_SECRET" }
}
```

This cleanup is optional; leaving `env.CLAW_LEDGER_URL` is harmless.

### 23.3 Verify end-to-end

Start new Hestia and Athena sessions and run a health check in each. Both
should execute `ledger-api /health` with no approval prompts and return:

```json
{"status":"ok"}
```

## 24. Hestia "Tool exec not found" — two-layer exec security

If Hestia reports `Tool exec not found` on `ledger-api` calls after a gateway
update or config change, the fix is in `openclaw.json`.

**How exec security works (two layers):**

1. **Tool policy** (`agents.list[hestia].tools.allow`): must contain `"exec"` for the agent to invoke the exec tool at all. Secondary agents do not get exec by default.
2. **Exec-approvals** (`~/.openclaw/exec-approvals.json`): restricts which binaries actually run. Hestia's allowlist has `security: "allowlist"` — only `/usr/local/bin/ledger-api`, `/usr/bin/head`, and `/usr/bin/ls` are permitted.

**The correct config** in `~/.openclaw/openclaw.json` under the hestia agent:

```json
"tools": {
  "allow": ["exec"]
}
```

`["ledger-api"]` looks intuitive but is wrong — `ledger-api` is not a plugin tool ID. A non-empty allow list with only unrecognized entries blocks exec entirely. Layer 2 (exec-approvals allowlist) is what scopes Hestia to the ledger binary only.
