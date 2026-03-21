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
  filtering (preserves current behavior).
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
  the 403 behavior.

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

## Task 5: Sprint closeout and deployment selection guide ✅ DONE

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
  receive HTTP 403; when unset, existing behavior is unchanged.
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

---

## Sprint 15 Closeout

### Shipped artifacts

| Task | Deliverable | Status |
|---|---|---|
| Task 1 | `deploy/systemd/` — service, sync, and DuckDNS unit files; RUNBOOK.md Section 12 | ✅ Shipped |
| Task 2 | `deploy/docker/` — Dockerfile, docker-compose.yml, .dockerignore, README; RUNBOOK.md Section 13 | ✅ Shipped |
| Task 3 | `CLAW_WEBHOOK_ALLOWED_IPS` / `CLAW_TRUSTED_PROXIES` middleware; `doctor` allowlist check; RUNBOOK.md Section 9.6; ARCHITECTURE.md webhook section; tests | ✅ Shipped |
| Task 4 | `deploy/proxy/` — Caddyfile.example, nginx-mtls.conf.example, authelia-notes.md; RUNBOOK.md Section 14; ARCHITECTURE.md auth boundary section | ✅ Shipped |
| Task 5 | RUNBOOK.md Section 15 (deployment selection guide); ARCHITECTURE.md M13 closeout note; ROADMAP.md M13 moved to completed | ✅ Shipped |

### Quality-gate evidence (final integration)

All four commands passed on the final sprint branch:

```
uv run --locked ruff format . --check   ✅
uv run --locked ruff check .            ✅
uv run --locked mypy .                  ✅
uv run --locked pytest -v               ✅
```

### Deferred items carried to M14

- Automated TLS certificate provisioning within `ledger serve` (TLS remains
  the reverse proxy's responsibility).
- `ledger doctor --fix` auto-remediation flows.
- Kubernetes / Helm deployment (out of scope for this home-server project).
- Guaranteed agent-to-agent orchestration (deferred from Sprint 14; remains
  unscheduled).

---

# Sprint 16 — M14: API Quality-of-Life & Skill Discovery

## Sprint goal

Ship four focused improvements surfaced during the first production run of the
two-agent household. No schema changes. Every task is independently releasable.

## Why this sprint exists

M0–M13 delivered a full, production-deployed ledger. Usage revealed a set of
ergonomic gaps: annotation writes require a follow-up GET to confirm state,
agents must guess at valid category/tag vocabulary, every `/spend` call requires
manually computing dates, and newly pushed skills are invisible to agents until
TOOLS.md is updated by hand. M14 closes all four gaps with minimal surface area.

## Working agreements

- Each task ships as its own PR; no task blocks another.
- All Python changes must pass the quality gate before merge:
  - `uv run --locked ruff format . --check`
  - `uv run --locked ruff check .`
  - `uv run --locked mypy .`
  - `uv run --locked pytest -v`
- Script-only tasks (Task 4) still run the gate to confirm no regressions.
- Mark completed tasks `✅ DONE` in this file before committing.

---

## Task 1: BUG-006 — PUT /annotations returns the updated transaction record

### Background

`PUT /annotations/{transaction_id}` currently returns `{"status": "ok"}`.
Callers must issue a follow-up `GET /transactions/{id}` to see the final merged
state. Returning the full record in the PUT response eliminates that round trip.

### Scope

**`server.py` — `PUT /annotations/{transaction_id}`**

After successfully upserting the annotation, fetch and return the full
transaction record using the same logic as `GET /transactions/{id}`. The
response body, status code (200), and error behaviour (404 when transaction
does not exist) are otherwise unchanged.

**Response shape** — identical to `GET /transactions/{id}`:

```json
{
  "id": "txn_abc123",
  "account_id": "acc_xyz",
  "amount": 42.50,
  "iso_currency_code": "USD",
  "name": "COFFEE SHOP",
  "merchant_name": "COFFEE SHOP",
  "pending": false,
  "date": "2026-03-01",
  "raw_json": null,
  "suppressed_by": null,
  "annotation": {
    "category": "food",
    "note": "morning coffee",
    "tags": ["discretionary"],
    "updated_at": "2026-03-21T09:00:00+00:00"
  }
}
```

The `annotation` block reflects the values just written — it will never be
`null` in a successful PUT response because the upsert just created it.

**Implementation note:** avoid duplicating the fetch logic. If `GET
/transactions/{id}` is implemented as a helper function or the fetch SQL is
already factored out in `db.py`, reuse it. If it is currently inlined in the
route handler, extract it into a shared helper as part of this task.

**`tests/test_server.py`**

Update or add tests covering:

- Successful PUT returns HTTP 200 with the full transaction shape (not
  `{"status": "ok"}`).
- The returned annotation block contains the values that were just written.
- PUT on a non-existent transaction still returns HTTP 404.
- A second PUT (update, not create) returns the newly updated annotation fields.

### Done when

- Quality gate passes.
- `PUT /annotations/{id}` response body matches `GET /transactions/{id}` shape.
- No follow-up GET is needed to confirm the write.

---

## Task 2: BUG-007 — GET /categories and GET /tags vocabulary endpoints

### Background

Agents annotating transactions must infer valid category/tag values from
sampled transaction data. This leads to duplicates (`groceries` vs `grocery`).
Two read-only endpoints expose the vocabulary already present in annotations.

### Scope

**`server.py` — two new GET endpoints**

Both endpoints require the standard bearer-token auth (same as all other
endpoints). Both are read-only and require no request body or query parameters.

---

**`GET /categories`**

Returns the distinct set of non-null `category` values present across all rows
in the `annotations` table, sorted alphabetically (case-insensitive).

Response shape:

```json
{
  "categories": ["food", "software", "transport", "utilities"]
}
```

- Empty array if no annotations have a category set.
- SQL: `SELECT DISTINCT category FROM annotations WHERE category IS NOT NULL ORDER BY category COLLATE NOCASE`

---

**`GET /tags`**

Returns the distinct set of tag values across all annotations. Because `tags`
is stored as a JSON array string per row, the query must unnest using SQLite's
`json_each`:

```sql
SELECT DISTINCT j.value
FROM annotations a, json_each(a.tags) j
WHERE a.tags IS NOT NULL
ORDER BY j.value COLLATE NOCASE
```

Response shape:

```json
{
  "tags": ["discretionary", "needs-athena-review", "recurring", "subscription"]
}
```

- Empty array if no annotations have tags set.

---

**`db.py` — two new query functions**

Add `get_distinct_categories(conn) -> list[str]` and
`get_distinct_tags(conn) -> list[str]`. Keep the SQL in `db.py`; keep
the HTTP wiring in `server.py`.

**`tests/test_server.py`**

- `GET /categories` returns an alphabetically sorted list of distinct categories
  from existing annotations; excludes null categories.
- `GET /tags` returns an alphabetically sorted flat list of distinct tag values
  unnested from all annotation rows; excludes null/empty tags arrays.
- Both return `{"categories": []}` / `{"tags": []}` when the annotations
  table has no relevant data.
- Both require auth (unauthenticated request returns 401).

### Done when

- Quality gate passes.
- Both endpoints are listed in the auto-generated OpenAPI spec at `/openapi.json`.
- An agent can call `GET /categories` immediately after startup to retrieve
  the current vocabulary without sampling transactions.

---

## Task 3: BUG-010 — GET /spend relative date range shorthand

### Background

Every `/spend` call requires `start_date` and `end_date` in ISO format. Common
queries like "last month" force callers to compute and format both dates, which
is tedious interactively and verbose in agent prompts.

### Scope

**`server.py` — extend `GET /spend`**

Add an optional `range` query parameter:

```
range: Literal["last_month", "this_month", "last_30_days", "last_7_days"] | None = None
```

**Resolution rules (applied server-side at request time):**

1. If `range` is supplied, compute `start_date` and `end_date` from it using
   **server local time** (i.e. `datetime.now().date()`, not UTC).
2. If `start_date` is also supplied explicitly, it overrides the range-derived
   start date.
3. If `end_date` is also supplied explicitly, it overrides the range-derived
   end date.
4. If `range` is absent and either `start_date` or `end_date` is missing,
   return HTTP 422 with a clear message (existing behavior is unchanged).

**Date computation definitions (server local time):**

| `range` value  | `start_date`                          | `end_date`       |
|----------------|---------------------------------------|------------------|
| `this_month`   | First day of current month            | Today            |
| `last_month`   | First day of previous calendar month  | Last day of previous calendar month |
| `last_30_days` | Today − 30 days (inclusive)           | Today            |
| `last_7_days`  | Today − 7 days (inclusive)            | Today            |

"Previous calendar month" crosses year boundaries correctly (e.g. January →
December of the prior year).

**Response shape** — unchanged. Optionally surface the resolved dates in the
response body so callers can see what window was used:

```json
{
  "start_date": "2026-02-01",
  "end_date": "2026-02-28",
  "total_spend": 1842.00,
  "transaction_count": 34,
  "includes_pending": false,
  "filters": {
    "owner": null,
    "tags": []
  }
}
```

The `start_date` and `end_date` fields should always reflect the resolved
dates (whether derived from `range` or supplied explicitly) so callers can
confirm what window was actually used.

**Validation:**

- `range` with an unrecognised value → HTTP 422 (FastAPI handles this
  automatically via `Literal`).
- `range` absent, `start_date` present, `end_date` absent → HTTP 422.
- `range` absent, `end_date` present, `start_date` absent → HTTP 422.

**`tests/test_server.py`**

- `?range=this_month` returns a 200 with `start_date` set to the first of the
  current month and `end_date` set to today.
- `?range=last_month` returns the correct first/last day of the prior month,
  including the January→December year-boundary case.
- `?range=last_30_days` and `?range=last_7_days` return the correct computed
  window.
- Explicit `start_date` overrides the range-derived start; explicit `end_date`
  overrides the range-derived end.
- Omitting both `range` and `start_date`/`end_date` returns HTTP 422.
- An unrecognised `range` value returns HTTP 422.
- Existing calls with explicit `start_date` + `end_date` (no `range`) are
  unaffected.

**Tip:** freeze `datetime.now()` in tests (e.g. via `freezegun` or
`unittest.mock.patch`) so date-window assertions are deterministic.

### Done when

- Quality gate passes.
- `GET /spend?range=last_month` works end-to-end with no explicit dates.
- Explicit dates still work unchanged.
- Resolved `start_date` / `end_date` are visible in the response.

---

## Task 4: BUG-004 — Agent skill auto-discovery via sync-skills.sh

### Background

After `sync-skills.sh push` copies a skill bundle into an agent's skills
directory, the agent has no awareness of it. The agent's `TOOLS.md` must be
updated manually. Agents have no skills registered in their `TOOLS.md` today.

This task extends `sync-skills.sh push` to automatically inject (or update)
a `## Skills` section in each target agent's `TOOLS.md`, and adds a RUNBOOK
section documenting the workflow.

### Scope

**`scripts/sync-skills.sh` — extend `push`**

After the `rsync` copy step for each skill, upsert a skills entry in the target
agent's `TOOLS.md`.

The injected block must be:

- **Idempotent:** running `push` twice produces the same result. Use sentinel
  comments to identify the auto-generated block so it can be replaced, not
  appended. Recommended markers:
  ```
  <!-- sync-skills-start -->
  ...
  <!-- sync-skills-end -->
  ```
- **Derived from `SKILL.md` frontmatter only.** Read the following fields from
  the skill's `SKILL.md` YAML frontmatter block:
  - `name` — used as the section heading
  - `description` — one-line description
  - `metadata.openclaw.requires.env` — list of required environment variable
    names

The injected entry format (one entry per skill under the sentinel block):

```markdown
## Skills (managed by sync-skills.sh — do not edit between markers)

<!-- sync-skills-start -->
### hestia-ledger
- **Description:** Ingest and annotate claw-plaid-ledger transactions. Use after a Plaid sync to process new transactions, apply deterministic annotations, and escalate uncertain items to Athena via needs-athena-review tags. Reads and writes via the ledger HTTP API using bearer-token auth.
- **Skill path:** ~/.openclaw/workspace/agents/hestia/skills/hestia-ledger/SKILL.md
- **Required env:** `CLAW_API_SECRET`, `CLAW_LEDGER_URL`
<!-- sync-skills-end -->
```

If an agent has multiple skills, all entries appear between the same pair of
sentinels.

- **Non-destructive toward content outside the sentinel block.** The operator's
  hand-written TOOLS.md notes (cameras, SSH hosts, etc.) must be preserved.
- **Creates `TOOLS.md` if absent.** If the target agent directory exists but
  has no `TOOLS.md`, create one containing only the injected block (no default
  boilerplate).
- **`pull` behaviour:** leave the injected block in place on pull. The script
  does not strip or modify TOOLS.md on pull.

**Frontmatter parsing:** The SKILL.md files use YAML frontmatter delimited by
`---`. The script may use Python (`python3 -c "..."`) for parsing rather than
trying to wrangle YAML in pure bash — this is the recommended approach given
the nested structure of `metadata.openclaw.requires.env`.

**RUNBOOK.md — new section: Skill registration**

Add a section (placement: after the existing skills/sync-skills content, or
as a new top-level section if one does not exist) covering:

- What `sync-skills.sh push` now does end-to-end: copies skill files AND
  updates the agent's TOOLS.md.
- How to verify the injection worked: inspect
  `~/.openclaw/workspace/agents/<agent>/TOOLS.md` and confirm the `## Skills`
  section is present.
- How to re-run push after updating a skill definition (idempotent; safe to
  re-run at any time).
- Manual fallback: if `sync-skills.sh` is unavailable, the operator can
  copy the template below into the agent's TOOLS.md and fill in the fields
  from SKILL.md frontmatter.

  ```markdown
  ## Skills (managed by sync-skills.sh — do not edit between markers)

  <!-- sync-skills-start -->
  ### <name>
  - **Description:** <description from SKILL.md>
  - **Skill path:** ~/.openclaw/workspace/agents/<agent>/skills/<name>/SKILL.md
  - **Required env:** `VAR1`, `VAR2`
  <!-- sync-skills-end -->
  ```

**No Python source changes.** This task is entirely in `scripts/` and
`RUNBOOK.md`. The quality gate still runs to confirm no regressions.

### Done when

- `./scripts/sync-skills.sh push` copies skill files and upserts the `##
  Skills` block in each agent's TOOLS.md.
- Running push a second time produces no diff in TOOLS.md (idempotent).
- Content outside the sentinel markers is untouched.
- RUNBOOK.md documents the workflow and the manual fallback template.
- Quality gate passes.

---

## Acceptance criteria for Sprint 16

- `PUT /annotations/{id}` returns the full transaction record; no follow-up
  GET required.
- `GET /categories` and `GET /tags` return sorted, deduplicated vocabulary
  arrays from existing annotations.
- `GET /spend?range=last_month` (and the other three shorthands) works without
  explicit dates; resolved dates appear in the response.
- `sync-skills.sh push` idempotently registers skills in each agent's TOOLS.md.
- All quality-gate commands pass on every merged PR.

## Explicitly deferred (out of scope for Sprint 16)

- `GET /spend` filters by `account_id`, `category`, or `tag` (M15 scope;
  depends on BUG-005 account labels).
- `GET /spend/trends` month-over-month endpoint (M16 scope).
- `ledger doctor --fix` auto-remediation (M18 scope).
- Automated TLS provisioning within `ledger serve`.
