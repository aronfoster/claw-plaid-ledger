#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Reinstalling ledger from $REPO_ROOT..."
uv tool install --reinstall "$REPO_ROOT"

echo "Installing ledger-api wrapper..."
sudo install -m 755 "$(dirname "$0")/ledger-api" /usr/local/bin/ledger-api

echo "Restarting claw-plaid-ledger service..."
sudo systemctl restart claw-plaid-ledger

echo "Done. Service status:"
systemctl status claw-plaid-ledger --no-pager -l
