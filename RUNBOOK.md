# Production Operations Runbook — M8

## 1. Purpose and scope

This runbook covers the steps an operator needs to move
`claw-plaid-ledger` from Plaid sandbox to a live production environment
and to validate the setup before the first real sync.

**In scope for M7:**

- Obtaining Plaid production API access
- Configuring and isolating the production environment
- Running `ledger doctor --production-preflight` before first live sync
- Performing a first live sync and validating the result
- Backup and recovery procedures for SQLite and secrets
- Incident triage quick reference

**Explicitly out of scope for M7:**

- Automated Plaid Link / OAuth flow (manual token capture only)
- Multi-item household ingestion workflow expansion (M8)
- Canonical overlap suppression across institutions (M9)
- Multi-item webhook automation / routing changes (M10)

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
      server).
- [ ] The **webhook signing secret** is noted from the dashboard
      (Webhooks → Signing secret).  This is the `PLAID_WEBHOOK_SECRET`
      value.

### 2.2 Required Plaid production credentials

| Credential | Where to find it | Env var |
|---|---|---|
| Client ID | Dashboard → Team Settings → Keys | `PLAID_CLIENT_ID` |
| Production secret | Dashboard → Team Settings → Keys | `PLAID_SECRET` |
| Access token(s) | Returned by Link flow, store securely | `PLAID_ACCESS_TOKEN` (single-item) or per-item env var (multi-item) |
| Webhook signing secret | Dashboard → Webhooks → Signing secret | `PLAID_WEBHOOK_SECRET` |

> **Do not use sandbox credentials for production syncs.**  The
> `PLAID_ENV` value must be `production` (not `sandbox`) when using live
> bank connections.

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

## 8. Incident appendix

### 8.1 Invalid or expired access token

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

### 8.2 Webhook signature mismatch

**Symptoms:** `POST /webhooks/plaid` returns HTTP 400; logs show
"invalid Plaid webhook signature".

**Triage:**

1. Confirm `PLAID_WEBHOOK_SECRET` matches the signing secret in the
   Plaid dashboard (Webhooks → Signing secret).
2. If the secret was recently rotated in the dashboard, update the env
   var and restart `ledger serve`.
3. Verify the server is receiving the full raw request body unmodified
   (no middleware should alter the body before signature verification).

### 8.3 Stale cursor concerns

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

### 8.4 Accidental wrong-environment configuration

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
