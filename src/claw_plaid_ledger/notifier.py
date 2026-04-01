"""OpenClaw notification sender."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claw_plaid_ledger.config import OpenClawConfig
    from claw_plaid_ledger.sync_engine import SyncSummary

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def notify_openclaw(summary: SyncSummary, config: OpenClawConfig) -> None:
    """
    Send a POST to OpenClaw's /hooks/agent endpoint to wake the agent.

    Does nothing (logs a warning) when token is None or empty, or when
    the configured URL uses a scheme other than http or https.
    Never propagates exceptions.
    """
    if not config.token:
        logger.warning("OPENCLAW_HOOKS_TOKEN not set — skipping notification")
        return

    scheme = urllib.parse.urlsplit(config.url).scheme
    if scheme not in _ALLOWED_SCHEMES:
        logger.warning(
            "OpenClaw URL has unsupported scheme %r — skipping notification",
            scheme,
        )
        return

    parts = []
    if summary.added:
        parts.append(f"{summary.added} added")
    if summary.modified:
        parts.append(f"{summary.modified} modified")
    if summary.removed:
        parts.append(f"{summary.removed} removed")

    message = (
        "Plaid sync complete: "
        + ", ".join(parts)
        + ". Hestia should run ingestion allocation updates; Athena reviews"
        " later on schedule or anomaly flags."
    )

    payload = {
        "message": message,
        "name": config.agent,
        "wakeMode": config.wake_mode,
    }

    data = json.dumps(payload).encode()
    # S310: the scheme has been validated to be http or https on the lines
    # above; file: and custom schemes are already rejected with a warning.
    # ruff cannot perform flow analysis to see this, so the suppression is
    # narrow (single line, single code) and the root cause is addressed.
    req = urllib.request.Request(  # noqa: S310
        config.url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.token}",
        },
        method="POST",
    )

    try:
        # S310: same rationale as the Request() call above — scheme already
        # validated.  ruff fires on urlopen regardless of argument type.
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            status = resp.status
    except urllib.error.HTTPError as e:
        logger.warning("OpenClaw notification failed: HTTP %s", e.code)
        return
    except urllib.error.URLError as e:
        logger.warning("OpenClaw notification failed (network): %s", e)
        return

    logger.info("OpenClaw notification sent: %s", status)
