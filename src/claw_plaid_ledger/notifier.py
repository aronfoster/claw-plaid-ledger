"""OpenClaw notification sender."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


# PLR0913: The function signature is mandated by the sprint spec (Task 2).
# All seven keyword-only parameters are required by the caller in server.py
# and cannot be collapsed into a config object without changing the public API
# defined in the sprint.  Remove this bypass if/when the API is revised.
def notify_openclaw(  # noqa: PLR0913
    *,
    added: int,
    modified: int,
    removed: int,
    url: str,
    token: str | None,
    agent: str,
    wake_mode: str,
) -> None:
    """
    Send a POST to OpenClaw's /hooks/agent endpoint to wake the agent.

    Does nothing (logs a warning) when token is None or empty.
    Never propagates exceptions.
    """
    if not token:
        logger.warning("OPENCLAW_HOOKS_TOKEN not set — skipping notification")
        return

    parts = []
    if added:
        parts.append(f"{added} added")
    if modified:
        parts.append(f"{modified} modified")
    if removed:
        parts.append(f"{removed} removed")

    message = (
        "Plaid sync complete: "
        + ", ".join(parts)
        + ". Review new transactions and annotate as appropriate."
    )

    payload = {
        "message": message,
        "name": agent,
        "wakeMode": wake_mode,
    }

    data = json.dumps(payload).encode()
    # S310: urllib.request is the stdlib HTTP client mandated by the sprint
    # conventions (no httpx at runtime).  The URL is operator-supplied via
    # OPENCLAW_HOOKS_URL; file: and custom schemes are not a concern here.
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        # S310: same rationale as the Request() call above.
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            status = resp.status
    except urllib.error.HTTPError as e:
        logger.warning("OpenClaw notification failed: HTTP %s", e.code)
        return
    except urllib.error.URLError as e:
        logger.warning("OpenClaw notification failed (network): %s", e)
        return

    logger.info("OpenClaw notification sent: %s", status)
