"""Tests for IP resolution helpers and WebhookIPAllowlistMiddleware."""

from __future__ import annotations

import hashlib
import hmac
import http
import ipaddress
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from claw_plaid_ledger.server import _ip_in_allowlist, _resolve_client_ip, app

if TYPE_CHECKING:
    import pathlib

    import pytest

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105
_WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105


def _make_plaid_sig(body: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    """Compute a valid Plaid-Verification HMAC-SHA256 hex digest."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestResolveClientIp:
    """Unit tests for _resolve_client_ip."""

    def _make_request(
        self,
        host: str = "127.0.0.1",
        xff: str | None = None,
    ) -> MagicMock:
        """Return a mock Request with the given client host and XFF header."""
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = host
        headers: dict[str, str] = {}
        if xff is not None:
            headers["x-forwarded-for"] = xff
        req.headers = headers
        return req

    def test_direct_ip_not_trusted_returns_direct(self) -> None:
        """Direct IP not in trusted_proxies is returned as-is."""
        req = self._make_request(host="10.0.0.5")
        trusted: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
            ipaddress.IPv4Address("127.0.0.1")
        ]

        result = _resolve_client_ip(req, trusted)

        assert result == ipaddress.IPv4Address("10.0.0.5")

    def test_trusted_proxy_with_xff_returns_leftmost(self) -> None:
        """Trusted proxy with X-Forwarded-For returns leftmost address."""
        req = self._make_request(
            host="127.0.0.1",
            xff="52.21.1.100, 10.0.0.1",
        )
        trusted: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
            ipaddress.IPv4Address("127.0.0.1")
        ]

        result = _resolve_client_ip(req, trusted)

        assert result == ipaddress.IPv4Address("52.21.1.100")

    def test_trusted_proxy_without_xff_returns_direct(self) -> None:
        """Trusted proxy with no X-Forwarded-For returns direct IP."""
        req = self._make_request(host="127.0.0.1")
        trusted: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
            ipaddress.IPv4Address("127.0.0.1")
        ]

        result = _resolve_client_ip(req, trusted)

        assert result == ipaddress.IPv4Address("127.0.0.1")

    def test_unparseable_direct_ip_falls_back_to_loopback(self) -> None:
        """Unparseable direct IP (e.g. 'testclient') falls back to loopback."""
        req = self._make_request(host="testclient")
        trusted: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
            ipaddress.IPv4Address("127.0.0.1")
        ]

        result = _resolve_client_ip(req, trusted)

        # testclient → fallback 127.0.0.1 which is trusted → no XFF → 127.0.0.1
        assert result == ipaddress.IPv4Address("127.0.0.1")

    def test_no_client_falls_back_to_loopback(self) -> None:
        """Missing request.client falls back to 127.0.0.1."""
        req = MagicMock()
        req.client = None
        req.headers = {}
        loopback: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
            ipaddress.IPv4Address("127.0.0.1")
        ]

        result = _resolve_client_ip(req, loopback)

        assert result == ipaddress.IPv4Address("127.0.0.1")


class TestIpInAllowlist:
    """Unit tests for _ip_in_allowlist."""

    def test_ip_in_network_returns_true(self) -> None:
        """IP that falls within a CIDR returns True."""
        ip = ipaddress.IPv4Address("52.21.5.100")
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
            ipaddress.ip_network("52.21.0.0/16"),
        ]

        assert _ip_in_allowlist(ip, networks) is True

    def test_ip_not_in_network_returns_false(self) -> None:
        """IP outside all CIDRs returns False."""
        ip = ipaddress.IPv4Address("10.0.0.1")
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
            ipaddress.ip_network("52.21.0.0/16"),
        ]

        assert _ip_in_allowlist(ip, networks) is False

    def test_empty_network_list_returns_false(self) -> None:
        """Empty allowlist always returns False."""
        ip = ipaddress.IPv4Address("127.0.0.1")
        assert _ip_in_allowlist(ip, []) is False

    def test_ip_matches_second_network(self) -> None:
        """IP in the second network (not the first) returns True."""
        ip = ipaddress.IPv4Address("3.211.5.1")
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
            ipaddress.ip_network("52.21.0.0/16"),
            ipaddress.ip_network("3.211.0.0/16"),
        ]

        assert _ip_in_allowlist(ip, networks) is True


class TestWebhookIPAllowlistMiddleware:
    """Integration tests for the webhook IP allowlist middleware."""

    def test_no_allowlist_configured_webhook_passes_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Middleware is transparent when CLAW_WEBHOOK_ALLOWED_IPS is unset."""
        db_path = str(tmp_path / "test.db")
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", db_path)
        monkeypatch.delenv("CLAW_WEBHOOK_ALLOWED_IPS", raising=False)

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", AsyncMock()
        )

        response = client.post(
            "/webhooks/plaid",
            content=body,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Plaid-Verification": sig,
                "Content-Type": "application/json",
            },
        )

        # Middleware is transparent when no allowlist is configured.
        assert response.status_code != http.HTTPStatus.FORBIDDEN

    def test_allowlist_configured_matching_ip_passes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Request from an allowlisted IP passes the middleware."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )
        # TestClient direct IP falls back to 127.0.0.1 (trusted); no XFF →
        # resolved IP = 127.0.0.1.  Allow 127.0.0.1/32.
        monkeypatch.setenv("CLAW_WEBHOOK_ALLOWED_IPS", "127.0.0.1/32")
        monkeypatch.setenv("CLAW_TRUSTED_PROXIES", "127.0.0.1")

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", AsyncMock()
        )

        response = client.post(
            "/webhooks/plaid",
            content=body,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Plaid-Verification": sig,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code != http.HTTPStatus.FORBIDDEN

    def test_allowlist_configured_non_matching_ip_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Request from a non-allowlisted IP receives HTTP 403."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )
        # TestClient direct IP → 127.0.0.1; allowed range excludes it.
        monkeypatch.setenv("CLAW_WEBHOOK_ALLOWED_IPS", "52.21.0.0/16")
        monkeypatch.setenv("CLAW_TRUSTED_PROXIES", "10.0.0.1")

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        response = client.post(
            "/webhooks/plaid",
            content=body,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Plaid-Verification": sig,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == http.HTTPStatus.FORBIDDEN
        assert response.json() == {"detail": "forbidden"}

    def test_xff_resolves_to_allowed_ip_passes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """X-Forwarded-For from trusted proxy resolves to allowlisted IP."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )
        monkeypatch.setenv("CLAW_WEBHOOK_ALLOWED_IPS", "52.21.0.0/16")
        # TestClient falls back to 127.0.0.1 as direct IP, which is trusted.
        monkeypatch.setenv("CLAW_TRUSTED_PROXIES", "127.0.0.1")

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", AsyncMock()
        )

        response = client.post(
            "/webhooks/plaid",
            content=body,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Plaid-Verification": sig,
                "Content-Type": "application/json",
                # Leftmost XFF is a Plaid IP in the allowed range.
                "X-Forwarded-For": "52.21.5.1, 10.0.0.1",
            },
        )

        assert response.status_code != http.HTTPStatus.FORBIDDEN

    def test_xff_resolves_to_blocked_ip_returns_403(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """XFF from trusted proxy that resolves to non-allowlisted IP → 403."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )
        monkeypatch.setenv("CLAW_WEBHOOK_ALLOWED_IPS", "52.21.0.0/16")
        monkeypatch.setenv("CLAW_TRUSTED_PROXIES", "127.0.0.1")

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        response = client.post(
            "/webhooks/plaid",
            content=body,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Plaid-Verification": sig,
                "Content-Type": "application/json",
                # Leftmost XFF is not in the allowed range.
                "X-Forwarded-For": "10.99.0.1",
            },
        )

        assert response.status_code == http.HTTPStatus.FORBIDDEN
        assert response.json() == {"detail": "forbidden"}

    def test_allowlist_does_not_affect_other_routes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-webhook routes are unaffected when allowlist is configured."""
        monkeypatch.setenv("CLAW_WEBHOOK_ALLOWED_IPS", "52.21.0.0/16")
        monkeypatch.setenv("CLAW_TRUSTED_PROXIES", "10.0.0.1")

        response = client.get("/health")

        assert response.status_code == http.HTTPStatus.OK

    def test_blocked_webhook_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: pathlib.Path,
    ) -> None:
        """Blocked webhook attempt is logged at WARNING level."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )
        monkeypatch.setenv("CLAW_WEBHOOK_ALLOWED_IPS", "52.21.0.0/16")
        monkeypatch.setenv("CLAW_TRUSTED_PROXIES", "10.0.0.1")

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        with caplog.at_level(
            logging.WARNING, logger="claw_plaid_ledger.server"
        ):
            client.post(
                "/webhooks/plaid",
                content=body,
                headers={
                    "Authorization": f"Bearer {_TOKEN}",
                    "Plaid-Verification": sig,
                    "Content-Type": "application/json",
                },
            )

        assert any("webhook IP blocked" in r.message for r in caplog.records)
