# Docker quickstart — claw-plaid-ledger

## Prerequisites

- Docker 24+ with Compose plugin (`docker compose version`)
- A valid `.env` file in this directory (see below)
- `items.toml` at `~/.config/claw-plaid-ledger/items.toml`

## 1. Create `.env`

Create `deploy/docker/.env` with at minimum:

```
PLAID_CLIENT_ID=...
PLAID_SECRET=...
CLAW_API_SECRET=...
CLAW_DB_PATH=/data/ledger.db
```

```bash
chmod 600 deploy/docker/.env
```

Never commit this file.

## 2. Start the service

```bash
cd deploy/docker && docker compose up -d
curl http://127.0.0.1:8000/health   # → {"status": "ok"}
```

## 3. View logs

```bash
docker compose logs -f ledger
```

## 4. Update to a new version

```bash
docker compose build --no-cache && docker compose up -d
```

## 5. Back up the database

```bash
docker run --rm \
  -v ledger-data:/data -v "$(pwd)":/backup \
  python:3.12-slim cp /data/ledger.db /backup/ledger.db.bak
```

## 6. Stop

```bash
docker compose down       # keeps ledger-data volume
docker compose down -v    # also removes volume (destructive!)
```

See **RUNBOOK.md Section 13** for the full Docker and LXC deployment guide.
