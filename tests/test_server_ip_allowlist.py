"""Unit tests for IP resolution and allowlist helpers."""

from __future__ import annotations

import ipaddress
from unittest.mock import MagicMock

from claw_plaid_ledger.middleware.ip_allowlist import (
    _ip_in_allowlist,
    _resolve_client_ip,
)


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
