"""Tests for Plaid webhook HMAC-SHA256 signature verification."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from claw_plaid_ledger.webhook_auth import verify_plaid_signature

if TYPE_CHECKING:
    import pytest

_SECRET = "test-webhook-secret"  # noqa: S105
_BODY = (
    b'{"webhook_type": "TRANSACTIONS",'
    b' "webhook_code": "SYNC_UPDATES_AVAILABLE"}'
)


def _make_signature(body: bytes, secret: str) -> str:
    """Compute the expected HMAC-SHA256 hex signature."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifyPlaidSignature:
    """Tests for verify_plaid_signature."""

    def test_valid_signature_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A correct HMAC-SHA256 signature returns True."""
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _SECRET)
        sig = _make_signature(_BODY, _SECRET)
        assert verify_plaid_signature(_BODY, {"Plaid-Verification": sig})

    def test_wrong_secret_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A signature computed with a different secret returns False."""
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _SECRET)
        sig = _make_signature(_BODY, "wrong-secret")
        assert not verify_plaid_signature(_BODY, {"Plaid-Verification": sig})

    def test_tampered_body_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A valid signature checked against a different body returns False."""
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _SECRET)
        sig = _make_signature(_BODY, _SECRET)
        tampered = _BODY + b" tampered"
        assert not verify_plaid_signature(
            tampered, {"Plaid-Verification": sig}
        )

    def test_missing_secret_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns False without raising when PLAID_WEBHOOK_SECRET is unset."""
        monkeypatch.delenv("PLAID_WEBHOOK_SECRET", raising=False)
        sig = _make_signature(_BODY, _SECRET)
        assert not verify_plaid_signature(_BODY, {"Plaid-Verification": sig})

    def test_missing_header_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns False when the Plaid-Verification header is absent."""
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _SECRET)
        assert not verify_plaid_signature(_BODY, {})

    def test_lowercase_header_name_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Accepts the header in lowercase form (HTTP/2 convention)."""
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _SECRET)
        sig = _make_signature(_BODY, _SECRET)
        assert verify_plaid_signature(_BODY, {"plaid-verification": sig})
