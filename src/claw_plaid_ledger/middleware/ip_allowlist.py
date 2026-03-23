"""Webhook IP allowlist middleware."""

from __future__ import annotations

import ipaddress
import logging
from typing import TYPE_CHECKING

import fastapi.responses
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from claw_plaid_ledger.config import ConfigError, load_config
from claw_plaid_ledger.logging_utils import get_correlation_id

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.responses import Response

logger = logging.getLogger(__name__)

_WEBHOOK_PATH = "/webhooks/plaid"


def _resolve_client_ip(
    request: Request,
    trusted_proxies: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """
    Return the real client IP, honoring X-Forwarded-For from proxies.

    If the direct connection address is in *trusted_proxies*, use the
    leftmost address in the ``X-Forwarded-For`` header as the real client
    IP.  Otherwise the direct connection address is returned as-is.
    """
    direct_ip_str = request.client.host if request.client else "127.0.0.1"
    try:
        direct_ip = ipaddress.ip_address(direct_ip_str)
    except ValueError:
        direct_ip = ipaddress.IPv4Address("127.0.0.1")

    if direct_ip not in trusted_proxies:
        return direct_ip

    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        leftmost = xff.split(",")[0].strip()
        try:
            return ipaddress.ip_address(leftmost)
        except ValueError:
            pass

    return direct_ip


def _ip_in_allowlist(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Return True if *ip* falls within any network in *networks*."""
    return any(ip in net for net in networks)


class WebhookIPAllowlistMiddleware(BaseHTTPMiddleware):
    """
    Block non-allowlisted IPs from POST /webhooks/plaid.

    Only active when ``CLAW_WEBHOOK_ALLOWED_IPS`` is configured in the
    environment.  All other routes are unaffected.

    IP resolution order:
    1. If the direct connection IP is in ``CLAW_TRUSTED_PROXIES`` (default:
       127.0.0.1), take the leftmost address from ``X-Forwarded-For`` as
       the real client IP.
    2. Otherwise use the direct connection IP.

    Blocked requests receive HTTP 403 ``{"detail": "forbidden"}`` and a
    WARNING-level log line with the resolved IP and ``request_id``.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Enforce the IP allowlist for POST /webhooks/plaid."""
        if request.method != "POST" or request.url.path != _WEBHOOK_PATH:
            return await call_next(request)

        try:
            config = load_config()
        except ConfigError:
            # Config unavailable — fail open so the existing auth layers
            # remain the last line of defense rather than a broken startup.
            return await call_next(request)

        if not config.webhook_allowed_ips:
            return await call_next(request)

        client_ip = _resolve_client_ip(request, config.trusted_proxies)
        if _ip_in_allowlist(client_ip, config.webhook_allowed_ips):
            return await call_next(request)

        request_id = get_correlation_id()
        logger.warning(
            "webhook IP blocked ip=%s request_id=%s",
            client_ip,
            request_id,
        )
        return fastapi.responses.JSONResponse(
            status_code=403,
            content={"detail": "forbidden"},
        )
