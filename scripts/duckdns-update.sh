#!/bin/sh
# Usage: DUCKDNS_TOKEN=<token> DUCKDNS_DOMAIN=<subdomain> ./duckdns-update.sh
# Updates the DuckDNS A record for the given subdomain to the current public IP.
# Suitable for cron or a systemd timer. Logs result to stdout.
#
# Cron example (update every 5 minutes):
#   */5 * * * * DUCKDNS_TOKEN=<token> DUCKDNS_DOMAIN=<subdomain> /path/to/duckdns-update.sh
set -eu
DUCKDNS_TOKEN="${DUCKDNS_TOKEN:?DUCKDNS_TOKEN is required}"
DUCKDNS_DOMAIN="${DUCKDNS_DOMAIN:?DUCKDNS_DOMAIN is required}"
RESULT=$(curl -fsSL \
  "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=")
echo "duckdns-update: ${DUCKDNS_DOMAIN} → ${RESULT}"
