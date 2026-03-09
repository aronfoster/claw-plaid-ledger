"""Plaid webhook HMAC-SHA256 signature verification."""

from __future__ import annotations

import hashlib
import hmac
import os


def verify_plaid_signature(body: bytes, headers: dict[str, str]) -> bool:
    """
    Verify a Plaid webhook HMAC-SHA256 signature.

    Reads ``PLAID_WEBHOOK_SECRET`` from the environment and checks that the
    ``Plaid-Verification`` request header matches
    ``HMAC-SHA256(body, secret)`` encoded as a lowercase hex digest.

    Fails closed: returns ``False`` whenever the secret is not set, the
    header is absent, or the digest does not match.  Never raises.
    """
    secret_raw = os.environ.get("PLAID_WEBHOOK_SECRET")
    if not secret_raw:
        return False

    signature = headers.get("Plaid-Verification") or headers.get(
        "plaid-verification"
    )
    if not signature:
        return False

    expected = hmac.new(secret_raw.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
