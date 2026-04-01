# Authelia OIDC/SSO front-proxy notes

This file is an operator-facing guide for protecting `ledger serve` with
[Authelia](https://www.authelia.com/) — an open-source authentication and
authorization server that provides OIDC, SSO, and multi-factor authentication
in front of your services.

> **Pointer, not a replacement** — this document gives you the key integration
> points.  For full Authelia configuration reference, see the upstream docs:
> <https://www.authelia.com/configuration/prologue/introduction/>

---

## When to choose Authelia vs mTLS

| Scenario | Recommended pattern |
|---|---|
| Agents (scripts, OpenClaw, cron jobs) calling the API on a home LAN | **mTLS** (see `Caddyfile.example` / `nginx-mtls.conf.example`) — cert-per-agent model, no browser required, works with any HTTP client that supports client certificates |
| Browser-based interactive access (Swagger UI, ad-hoc `curl`) by a human operator | **Authelia OIDC** — browser login flow, supports MFA, no cert management for the operator |
| Shared household access where multiple people need browser logins | **Authelia OIDC** — centralised user management, audit log, MFA per-user |
| Fully air-gapped LAN with no external IdP | **mTLS** — self-signed CA, no internet dependency |

The two patterns are not mutually exclusive.  Some operators run both: Authelia
protects the Swagger UI for interactive use while agents present client
certificates for programmatic access.

---

## How CLAW_API_SECRET and Authelia stack

Authelia governs **who can reach** `ledger serve` at the network layer.
`CLAW_API_SECRET` governs **who can call the API** at the application layer.
Both layers are always active:

```
Browser / agent
      │
      ▼
[Authelia auth layer]   ← session cookie / OIDC token checked here
      │ (forward-auth passes)
      ▼
[Caddy / nginx]         ← TLS termination, header forwarding
      │
      ▼
[ledger serve]          ← Bearer <CLAW_API_SECRET> checked here
```

- Agents making API calls must still include `Authorization: Bearer <CLAW_API_SECRET>`.
- Authelia adds a login wall for browser sessions; it does not replace the
  bearer token requirement.
- This is intentional: even if an Authelia session is somehow hijacked, the
  attacker still needs `CLAW_API_SECRET` to read financial data.

---

## Minimal Authelia configuration stubs

The snippets below assume Authelia is already installed and running.  They
show only the ledger-specific additions.

### 1. Access control rules (`configuration.yml`)

```yaml
access_control:
  default_policy: deny

  rules:
    # Allow unauthenticated health checks from monitoring tools.
    - domain: ledger.home.example
      policy: bypass
      resources:
        - "^/health$"

    # Require 2FA for the Swagger UI (interactive browser access).
    - domain: ledger.home.example
      policy: two_factor
      resources:
        - "^/docs.*$"
        - "^/openapi\\.json$"

    # Require 2FA for all API endpoints.
    - domain: ledger.home.example
      policy: two_factor
      resources:
        - "^/transactions.*$"
        - "^/spend.*$"
        - "^/webhooks/plaid.*$"
```

`two_factor` requires the user to complete both password and TOTP/WebAuthn
steps.  Change to `one_factor` if MFA is not yet configured.

### 2. Caddy forward-auth integration

Add the `forward_auth` directive inside your Caddy `ledger.home.example` site
block to delegate authentication to Authelia:

```caddyfile
ledger.home.example {
    # Authelia forward-auth — all requests pass through Authelia first.
    # Replace authelia.home.example with your Authelia host.
    forward_auth authelia.home.example:9091 {
        uri /api/authz/forward-auth
        copy_headers Remote-User Remote-Groups Remote-Email Remote-Name
    }

    # Bypass auth for /health so monitoring tools are not blocked.
    @health path /health
    handle @health {
        reverse_proxy 127.0.0.1:8000
    }

    handle {
        reverse_proxy 127.0.0.1:8000 {
            header_up X-Forwarded-For {remote_host}
        }
    }
}
```

> **Note:** The Caddy `forward_auth` directive requires the
> [caddy-authz](https://caddyserver.com/docs/modules/http.handlers.forward_auth)
> module, which is included in Caddy's standard build.

### 3. nginx `auth_request` integration

For nginx, use `auth_request` to call Authelia before serving the request:

```nginx
# Auth endpoint — Authelia validates the session cookie / bearer token.
location /authelia {
    internal;
    proxy_pass http://authelia:9091/api/authz/auth-request;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URL $scheme://$host$request_uri;
    proxy_set_header X-Original-Method $request_method;
    proxy_set_header X-Forwarded-For $remote_addr;
}

# Protected API location (example for /transactions).
location /transactions {
    auth_request /authelia;
    auth_request_set $authelia_user $upstream_http_remote_user;
    proxy_set_header Remote-User $authelia_user;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_pass http://127.0.0.1:8000;
}

# Unauthenticated /health bypass.
location = /health {
    proxy_pass http://127.0.0.1:8000;
}
```

Repeat the `auth_request` block for each protected location
(`/spend`, `/webhooks/plaid`).

---

## CLAW_TRUSTED_PROXIES when using Authelia

When Authelia and nginx/Caddy are involved, the `X-Forwarded-For` header is
set by the proxy layer.  In your `.env`:

```
# nginx or Caddy running on the same host as ledger serve:
CLAW_TRUSTED_PROXIES=127.0.0.1

# nginx or Caddy running on a different host (replace with actual IP):
CLAW_TRUSTED_PROXIES=10.0.0.1
```

This ensures that `CLAW_WEBHOOK_ALLOWED_IPS` (the webhook IP allowlist from
Task 3) resolves the real Plaid source IP rather than the proxy IP.

---

## Further reading

- Authelia installation guide: <https://www.authelia.com/integration/proxies/introduction/>
- Caddy integration: <https://www.authelia.com/integration/proxies/caddy/>
- nginx integration: <https://www.authelia.com/integration/proxies/nginx/>
- Access control reference: <https://www.authelia.com/configuration/security/access-control/>
