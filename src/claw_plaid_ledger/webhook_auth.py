"""Plaid webhook HMAC-SHA256 signature verification."""

from __future__ import annotations

import hashlib
import hmac
import logging

from claw_plaid_ledger.config import load_merged_env

logger = logging.getLogger(__name__)


def verify_plaid_signature(body: bytes, headers: dict[str, str]) -> bool:
    """
    Verify a Plaid webhook HMAC-SHA256 signature.

    Reads ``PLAID_WEBHOOK_SECRET`` from the merged environment (process env
    and ``~/.config/claw-plaid-ledger/.env``) and checks that the
    ``Plaid-Verification`` request header matches
    ``HMAC-SHA256(body, secret)`` encoded as a lowercase hex digest.

    When ``PLAID_WEBHOOK_SECRET`` is not set, logs INFO and returns ``True``
    (passthrough mode — verification is opt-in).  When the secret is set,
    fails closed: returns ``False`` if the header is absent or the digest
    does not match, and logs a WARNING for each failure mode.  Never raises.
    """
    secret_raw = load_merged_env().get("PLAID_WEBHOOK_SECRET")
    if not secret_raw:
        logger.info(
            "PLAID_WEBHOOK_SECRET not set; skipping signature verification"
        )
        return True

    signature = headers.get("Plaid-Verification") or headers.get(
        "plaid-verification"
    )
    if not signature:
        logger.warning(
            "verify_plaid_signature: Plaid-Verification header absent"
        )
        return False

    expected = hmac.new(secret_raw.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("verify_plaid_signature: HMAC digest mismatch")
        return False
    return True
