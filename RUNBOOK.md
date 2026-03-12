# Production Operations Runbook — M11

## 1. Purpose and scope

This runbook covers the steps an operator needs to move
`claw-plaid-ledger` from Plaid sandbox to a live production environment
and to validate the setup before the first real sync.

**In scope (M7 + M9 + M10 + M11):**

- Obtaining Plaid production API access
- Connecting institutions via the `ledger link` browser flow (M8)
- Household source precedence setup (`suppressed_accounts`,
  `ledger apply-precedence`, `ledger overlaps`) (M9)
- Configuring and isolating the production environment
- Running `ledger doctor --production-preflight` before first live sync
- Daily item health checks via `ledger items` and `ledger sync --all` (M8)
- Canonical-vs-raw transaction view behavior for agent/API consumers (M9)
- Multi-item webhook routing and scheduled sync fallback (M10)
- Stable public webhook URL setup with DuckDNS (M10)
- Request/sync correlation-ID tracing in logs (M11)
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



### Household source precedence setup (M9)

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
fallback (M10):

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
ledger.  Back it up with:

```bash
# Safe online backup (SQLite's built-in copy mechanism):
sqlite3 "$CLAW_PLAID_LEDGER_DB_PATH" ".backup $HOME/ledger-backup-$(date +%Y%m%d).db"

# Or simply copy the file when the server is not running:
cp "$CLAW_PLAID_LEDGER_DB_PATH" "$HOME/ledger-backup-$(date +%Y%m%d).db"
```

Automate this with a cron job or systemd timer.  Daily backups are
sufficient for most household use cases.

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

---

## 10. Stable webhook URL with DuckDNS

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
   (e.g. `myledger`) and click **add domain**.
3. Note the full hostname: `myledger.duckdns.org`.

### 10.3 Finding your DuckDNS token

After signing in, your token is displayed at the top of the DuckDNS
dashboard page.  It looks like a UUID (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).
Keep it secret — it grants write access to all your DuckDNS subdomains.

### 10.4 Updating the DNS record automatically

Use the included script to keep the DuckDNS record current:

```bash
DUCKDNS_TOKEN=<your-token> DUCKDNS_DOMAIN=myledger \
    ./scripts/duckdns-update.sh
```

For automatic updates, add a cron entry (edit with `crontab -e`):

```
*/5 * * * * DUCKDNS_TOKEN=<your-token> DUCKDNS_DOMAIN=myledger /path/to/scripts/duckdns-update.sh >> /var/log/duckdns.log 2>&1
```

A systemd timer is an equivalent alternative for systemd-based hosts.

### 10.5 Pointing Plaid to your webhook URL

Register the following URL in the Plaid dashboard
(Developers → Webhooks → Add webhook URL):

```
https://myledger.duckdns.org/webhooks/plaid
```

Replace `myledger` with your chosen subdomain.

### 10.6 Router and firewall port-forward requirements

`ledger serve` listens on plain HTTP internally (default port 8000,
configurable via `CLAW_SERVER_PORT`).  For Plaid to reach it you need:

1. **Port-forward** on your router: external TCP port 443 → internal
   IP of the server host, port `CLAW_SERVER_PORT` (or the port your
   reverse proxy listens on internally).
2. A **reverse proxy** (nginx, Caddy, or similar) to terminate TLS and
   forward requests to `ledger serve`.  Plaid requires HTTPS; the
   server itself speaks plain HTTP.

### 10.7 TLS termination

`ledger serve` does not handle TLS directly.  Recommended setup:

- **Caddy** — automatic HTTPS with Let's Encrypt, minimal config:

  ```
  myledger.duckdns.org {
      reverse_proxy localhost:8000
  }
  ```

- **nginx** — obtain a certificate with Certbot, then proxy:

  ```nginx
  server {
      listen 443 ssl;
      server_name myledger.duckdns.org;
      # ... ssl_certificate / ssl_certificate_key lines ...
      location / {
          proxy_pass http://127.0.0.1:8000;
      }
  }
  ```

### 10.8 Testing the webhook URL before registering with Plaid

Verify the full HTTPS path is reachable before entering it in the
dashboard:

```bash
curl -v https://myledger.duckdns.org/health
```

Expected response: `{"status": "ok"}` with HTTP 200.  If this fails,
check your port-forward, TLS configuration, and that `ledger serve` is
running.

---

## 11. Scheduled sync fallback

The scheduled sync fallback is an optional background loop (M10) that
automatically triggers a sync for any configured item that has not been
synced within a configurable window (default 24 hours).  It is the
safety net for missed or delayed webhooks.

Enable it by setting `CLAW_SCHEDULED_SYNC_ENABLED=true` in your `.env`
before starting `ledger serve`.  The fallback window is controlled by
`CLAW_SCHEDULED_SYNC_FALLBACK_HOURS` (default `24`; minimum `1`).

Run `ledger doctor` to confirm the current state:

```
scheduled-sync: ENABLED — fallback window 24h, check interval 60min
```

The loop is cancelled cleanly on server shutdown; no data is lost if
the server restarts mid-check.
