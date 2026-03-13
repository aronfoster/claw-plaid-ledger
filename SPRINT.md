# Sprint 15 — M13: Hardened Deployment & Local Security

## Sprint goal

Ship durable, production-grade deployment primitives for home-server operators.
By end of sprint, an operator can run `ledger serve` as a managed system service
(systemd), deploy it in a container (Docker/LXC), harden the webhook ingress
with server-side IP filtering, and harden the network auth boundary with a
reverse-proxy mTLS or OIDC front-proxy pattern.

## Why this sprint exists

Milestones M0–M12 delivered a fully functional, multi-item, two-agent-aware
ledger. The server is started manually with `ledger serve`, and deployment
guidance in RUNBOOK.md points to Caddy/nginx for TLS but stops short of
providing unit files or container definitions. M13 closes that gap so operators
can rely on OS-level service management and documented security hardening
patterns rather than ad-hoc shell sessions.

## Working agreements

- Keep each task reviewable in one PR where possible.
- Prefer augmenting existing RUNBOOK.md sections over creating orphaned docs.
- New Python code (config vars, middleware, tests) must pass the quality gate:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Tasks that are purely files/templates (no Python changes) still need a passing
  quality gate on the PR to confirm nothing was accidentally broken.

---

## Task 1: systemd service and timer unit files ✅ DONE

### Scope

Provide official systemd unit files so operators can run `ledger serve` as a
managed service and schedule syncs and DuckDNS updates via systemd timers rather
than cron.  This is the primary deployment method per the M13 roadmap.

### Required deliverables

- `deploy/systemd/claw-plaid-ledger.service` — systemd service for `ledger serve`
  - `Type=simple`, `Restart=on-failure`, `RestartSec=10`
  - `EnvironmentFile=` pointing to `~/.config/claw-plaid-ledger/.env` (with
    a comment directing operators to set the path for their OS / user)
  - `ExecStart` using the `ledger serve` entry point
  - A `[Install]` section with `WantedBy=multi-user.target`
- `deploy/systemd/claw-plaid-ledger-sync.service` + `.timer`
  - Oneshot service that runs `ledger sync --all`
  - Timer defaults to `OnCalendar=hourly` with `Persistent=true`
  - Intended as the scheduled-sync alternative for operators who prefer systemd
    over `CLAW_SCHEDULED_SYNC_ENABLED` (both approaches are valid; RUNBOOK
    explains the trade-offs)
- `deploy/systemd/claw-plaid-ledger-duckdns.service` + `.timer`
  - Oneshot service that runs `scripts/duckdns-update.sh`
  - Timer defaults to `OnCalendar=*:0/5` (every 5 minutes)
  - Supersedes the cron entry documented in current RUNBOOK.md Section 10.4
- RUNBOOK.md **Section 12 — Systemd deployment** covering:
  - Prerequisites (where to put unit files, how to reload the daemon)
  - Per-unit install and enable instructions
  - How to pass secrets securely via `EnvironmentFile` (mode 600 reminder)
  - `systemctl status`, `journalctl -u`, and restart commands for daily ops
  - Drop-in override pattern (`systemctl edit`) for site-specific customisation
  - Note on Proxmox LXC privilege considerations (if running in a container)

### Done when

- All three unit-file pairs are in `deploy/systemd/` with inline comments
  explaining each directive.
- An operator following RUNBOOK.md Section 12 can drop the files onto a
  Debian/Proxmox host, run `systemctl enable --now claw-plaid-ledger`, and
  see `ledger serve` running persistently.
- `journalctl -u claw-plaid-ledger` shows the server's structured log output.
- Quality gate passes (no Python changes expected; gate still runs on PR).

---

## Task 2: Container deployment examples (Docker / LXC) ✅ DONE

### Scope

Provide a minimal but production-appropriate Docker image definition and a
`docker-compose` example so operators who prefer container isolation can
deploy without writing their own Dockerfile.  Include brief LXC guidance for
Proxmox users who prefer OS-level containers.

### Required deliverables

- `deploy/docker/Dockerfile`
  - Multi-stage: `builder` stage uses `ghcr.io/astral-sh/uv` to install
    dependencies; `runtime` stage is a slim Python 3.12 image
  - Runs as a non-root user (`ledger`, UID 1000)
  - No secrets baked into the image; all config via environment variables
  - `ENTRYPOINT ["ledger", "serve"]`
- `deploy/docker/docker-compose.yml`
  - `env_file: .env` for secrets (never committed)
  - Named volume for the SQLite database (`ledger-data`)
  - Bind-mount for `items.toml` (`~/.config/claw-plaid-ledger/items.toml`)
  - Exposes port 8000 to `127.0.0.1` only (not `0.0.0.0`) by default
- `deploy/docker/.dockerignore` — excludes `.git`, `tests/`, `*.db`,
  `*.env`, `deploy/`, `skills/`
- `deploy/docker/README.md` (brief, ≤ 60 lines) — quickstart covering:
  - `docker compose up -d`
  - How to pass the `EnvironmentFile` path / `.env` file
  - Volume backup note (`docker run --rm` + `cp` pattern for the DB volume)
- RUNBOOK.md **Section 13 — Container deployment** covering:
  - Docker: build, run, and update workflow
  - LXC (Proxmox): recommended OS template, bind-mount for config dir,
    systemd-inside-LXC vs host-level service management note
  - Secrets management: never bake tokens into images; use Docker secrets or
    env_file with 600 permissions

### Done when

- `docker compose up` in `deploy/docker/` (with a valid `.env` file) starts
  `ledger serve` listening on `127.0.0.1:8000` inside the container.
- `curl http://127.0.0.1:8000/health` returns `{"status": "ok"}`.
- RUNBOOK.md Section 13 explains both Docker and LXC paths without ambiguity.
- Quality gate passes.

---

## Task 3: Webhook ingress hardening — server-side IP allowlisting ✅ DONE

### Scope

Add an opt-in server-side IP allowlist for `POST /webhooks/plaid` so operators
can enforce Plaid's published webhook source ranges in the application layer,
independent of (and complementary to) router-level firewall rules.  This closes
the gap between "Plaid HMAC signature verification" (cryptographic) and
"requests only reachable from expected sources" (network).

### Required deliverables

**Config (`config.py`)**

- `CLAW_WEBHOOK_ALLOWED_IPS` — optional, comma-separated list of IPv4/IPv6
  CIDRs (e.g. `"52.21.0.0/16,3.211.0.0/16"`).  Unset or empty = no IP
  filtering (preserves current behaviour).
- `CLAW_TRUSTED_PROXIES` — optional, comma-separated list of trusted reverse-
  proxy IPs for `X-Forwarded-For` resolution (default: `"127.0.0.1"`).  Used
  only when `CLAW_WEBHOOK_ALLOWED_IPS` is set.
- Both vars must be parsed into typed structures (list of `ipaddress.IPv4Network`
  / `IPv6Network`) at startup; invalid CIDRs raise `ConfigError`.

**Middleware (`server.py`)**

- When `CLAW_WEBHOOK_ALLOWED_IPS` is set, add a FastAPI middleware (or a
  `Depends` guard on the webhook route — choose whichever keeps the existing
  route signature cleanest) that:
  1. Extracts the real client IP: use the leftmost `X-Forwarded-For` address
     if the direct connection IP is in `CLAW_TRUSTED_PROXIES`; otherwise use
     the direct connection IP.
  2. Tests whether the resolved IP falls within any configured CIDR.
  3. Returns HTTP 403 with a plain JSON body `{"detail": "forbidden"}` if not.
  4. Logs the blocked attempt at `WARNING` level (include resolved IP and
     `request_id`; do not log the full request body).
- Requests to all other endpoints are unaffected.

**`doctor` extension (`cli.py` or `preflight.py`)**

- New entry: `webhook-allowlist: [OK] N CIDRs configured` or
  `webhook-allowlist: [WARN] no IP allowlist — POST /webhooks/plaid is
  reachable from any source IP`.
- The `[WARN]` state is not a failure (many valid deployments skip this);
  the message just surfaces the choice explicitly.

**RUNBOOK.md Section 9.6 — Webhook ingress security**

- Link to Plaid's published webhook IP documentation.
- Three-layer guidance: (1) `CLAW_WEBHOOK_ALLOWED_IPS` for app-layer
  enforcement, (2) router/firewall rules for network-layer enforcement,
  (3) Plaid HMAC verification for cryptographic authenticity.
- Note that `CLAW_TRUSTED_PROXIES` must be set correctly when behind Caddy,
  nginx, or another reverse proxy.

**ARCHITECTURE.md**

- Update the webhook ingress section to document the IP-resolution order and
  the 403 behaviour.

**Tests**

- `test_server.py`: allowlist middleware allows a matching IP, blocks a
  non-matching IP (HTTP 403), and is bypassed entirely when the env var is
  unset.  Test `X-Forwarded-For` resolution when a trusted proxy is configured.
- `test_config.py`: valid CIDRs parse correctly; invalid CIDR raises
  `ConfigError`; empty string is treated as "no filtering".
- `test_cli.py` (or `test_preflight.py`): `doctor` output includes the
  `webhook-allowlist` line in both configured and unconfigured states.

### Done when

- Quality gate passes (including new tests).
- `doctor` reports allowlist status in both states.
- Middleware blocks requests from unlisted IPs when configured and is
  transparent when not configured.
- RUNBOOK.md explains all three enforcement layers.

---

## Task 4: Local-network auth hardening — reverse-proxy examples ✅ DONE

### Scope

Provide concrete reverse-proxy configuration examples so operators can add a
network-layer auth boundary in front of `ledger serve` without guesswork.  The
two primary patterns are (a) mTLS client-certificate enforcement in Caddy or
nginx, and (b) an OIDC front-proxy (e.g. Authelia) for operators who prefer
SSO-style browser auth in front of the Swagger UI and API.

This task is primarily config examples and documentation; no Python source
changes are expected.

### Required deliverables

**`deploy/proxy/Caddyfile.example`**

- Caddy v2 config that:
  - Terminates TLS with a self-signed CA or Let's Encrypt
  - Enforces client certificate authentication on `/transactions`,
    `/annotations`, `/spend`, and `/webhooks/plaid`
  - Passes `GET /health`, `GET /docs`, and `GET /openapi.json` through
    without client cert (to allow unauthenticated health checks from
    monitoring tools)
  - Forwards `X-Forwarded-For` to `ledger serve`
- Inline comments explain each directive and the trust model

**`deploy/proxy/nginx-mtls.conf.example`**

- nginx equivalent of the Caddy example above using `ssl_verify_client on`
- Includes `proxy_set_header X-Forwarded-For $remote_addr` and the matching
  `CLAW_TRUSTED_PROXIES` value to set in `.env`

**`deploy/proxy/authelia-notes.md`**

- 1–2 page operator-facing guide covering:
  - When to choose Authelia (OIDC/SSO) over mTLS (cert-per-agent model)
  - The minimal Authelia `configuration.yml` stubs for protecting the
    `ledger serve` upstream
  - How the `CLAW_API_SECRET` bearer token and Authelia auth layer stack
    (operators still need the bearer token for API calls from agents; Authelia
    governs browser/interactive access)
  - Link to upstream Authelia docs; this file is a pointer, not a replacement

**RUNBOOK.md Section 14 — Auth hardening**

- Decision guide: when each pattern is appropriate (mTLS for agent-to-API
  calls on a home network; OIDC for browser-based ops and shared access)
- mTLS walkthrough: generating a self-signed CA, issuing a client cert, and
  configuring Caddy
- Notes on cert rotation and how to update the CA without downtime
- Reminder that `CLAW_API_SECRET` remains the application-layer guard in all
  configurations; network-layer hardening is additive

**ARCHITECTURE.md**

- Add an "Auth boundary" subsection documenting the two-layer model:
  1. Network layer: reverse proxy (mTLS or OIDC front-proxy) — optional,
     operator-configured
  2. Application layer: `CLAW_API_SECRET` bearer token — always required
- Update the component list to include "reverse proxy (optional)" as an
  architectural element

### Done when

- `deploy/proxy/` contains the three files with inline comments.
- RUNBOOK.md Section 14 lets an operator configure Caddy mTLS without
  consulting external docs for the basics.
- ARCHITECTURE.md clearly describes the two-layer auth model.
- Quality gate passes (no Python changes; gate still runs to confirm no
  regressions).

---

## Task 5: Sprint closeout and deployment selection guide

### Scope

Ensure the M13 deployment primitives are coherent from an operator's perspective,
add a deployment selection guide to help operators choose the right approach, and
close out the sprint.

### Required deliverables

- RUNBOOK.md **Section 15 — Deployment selection guide**
  - Decision table: bare `ledger serve` (dev/test) vs systemd (Linux/Proxmox
    production) vs Docker (containerised setup) vs LXC (Proxmox OS container)
  - Auth hardening decision: no proxy (simple home LAN) vs Caddy mTLS (agent
    access hardening) vs OIDC front-proxy (browser + shared access)
  - Cross-references to Sections 12, 13, and 14 for the chosen path
- ARCHITECTURE.md: update the "Current milestone focus" header to reflect M13
  completion and list the new deployment primitives added this sprint
- SPRINT.md: add a closeout section (shipped artifacts, deferred items,
  quality-gate evidence)

### Done when

- An operator can answer "how should I deploy this?" from RUNBOOK.md alone,
  using the selection guide to navigate to the right section.
- ARCHITECTURE.md is consistent with what was actually shipped.
- Deferred items (if any) are explicit, not implied.
- Quality gate passes on the final integration PR.

---

## Acceptance criteria for Sprint 15

- `deploy/systemd/`, `deploy/docker/`, and `deploy/proxy/` directories exist
  with the described files.
- An operator can deploy `ledger serve` under systemd using the provided unit
  files and RUNBOOK.md Section 12 alone (no tribal knowledge required).
- An operator can deploy using Docker Compose and RUNBOOK.md Section 13 alone.
- `CLAW_WEBHOOK_ALLOWED_IPS` is functional: when set, non-allowlisted IPs
  receive HTTP 403; when unset, existing behaviour is unchanged.
- `doctor` reports webhook allowlist status.
- `deploy/proxy/` examples and RUNBOOK.md Section 14 give an operator enough
  to configure Caddy mTLS without external research.
- All quality-gate commands pass on merged implementation PRs.

## Explicitly deferred (out of scope for Sprint 15)

- Automated TLS certificate provisioning within `ledger serve` itself
  (TLS remains the reverse proxy's responsibility).
- `ledger doctor --fix` auto-remediation flows (M14 scope).
- Guaranteed agent-to-agent orchestration (remains deferred from Sprint 14).
- Kubernetes / Helm deployment (not a target platform for this home-server
  project).
