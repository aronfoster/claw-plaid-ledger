"""
Tests for the local Plaid Link HTTP server.

The server is started on port 0 (OS-assigned) in each test to avoid port
conflicts.  All tests use real HTTP requests via urllib so the full request
handler path is exercised.
"""

from __future__ import annotations

import json
import urllib.request
from urllib.error import HTTPError

import pytest

from claw_plaid_ledger.link_server import (
    LINK_SERVER_HOST,
    LINK_SERVER_PORT,
    start_link_server,
)

_HTTP_OK = 200
_HTTP_NOT_FOUND = 404
_EXPECTED_PORT = 18790


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_link_server_host_is_loopback() -> None:
    """LINK_SERVER_HOST must be the loopback address."""
    assert LINK_SERVER_HOST == "127.0.0.1"


def test_link_server_port_value() -> None:
    """LINK_SERVER_PORT must be 18790."""
    assert LINK_SERVER_PORT == _EXPECTED_PORT


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_get_root_returns_200_with_html() -> None:
    """GET / returns 200 and the Plaid Link HTML page."""
    server, _done, _result = start_link_server("link-test-token", port=0)
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == _HTTP_OK
            content_type = resp.headers.get("Content-Type", "")
            assert "text/html" in content_type
            body = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in body
    finally:
        server.shutdown()


def test_get_root_injects_link_token_into_html() -> None:
    """GET / embeds the link_token as a JSON string in the HTML."""
    token = "link-sandbox-abc-123"  # noqa: S105
    server, _done, _result = start_link_server(token, port=0)
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            body = resp.read().decode("utf-8")
            # The token must appear JSON-encoded inside the HTML.
            assert json.dumps(token) in body
    finally:
        server.shutdown()


def test_get_unknown_path_returns_404() -> None:
    """GET on an unknown path returns 404."""
    server, _done, _result = start_link_server("tok", port=0)
    port = server.server_address[1]
    try:
        with pytest.raises(HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/unknown")
        assert exc_info.value.code == _HTTP_NOT_FOUND
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# POST /callback
# ---------------------------------------------------------------------------


def _post_callback(port: int, public_token: str) -> int:
    """POST public_token to /callback and return the HTTP status code."""
    payload = json.dumps({"public_token": public_token}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/callback",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        return int(resp.status)


def test_post_callback_returns_200() -> None:
    """POST /callback returns 200 OK."""
    server, _done, _result = start_link_server("tok", port=0)
    port = server.server_address[1]
    try:
        status = _post_callback(port, "public-sandbox-xyz")
        assert status == _HTTP_OK
    finally:
        server.shutdown()


def test_post_callback_captures_public_token() -> None:
    """POST /callback stores the public_token in the result list."""
    server, done, result = start_link_server("tok", port=0)
    port = server.server_address[1]
    try:
        _post_callback(port, "public-sandbox-abc")
        done.wait(timeout=2)
        assert result == ["public-sandbox-abc"]
    finally:
        server.shutdown()


def test_post_callback_sets_done_event() -> None:
    """POST /callback sets the done threading.Event."""
    server, done, _result = start_link_server("tok", port=0)
    port = server.server_address[1]
    assert not done.is_set()
    try:
        _post_callback(port, "public-sandbox-xyz")
        done.wait(timeout=2)
        assert done.is_set()
    finally:
        server.shutdown()


def test_post_unknown_path_returns_404() -> None:
    """POST on an unknown path returns 404."""
    server, _done, _result = start_link_server("tok", port=0)
    port = server.server_address[1]
    try:
        payload = b'{"public_token": "tok"}'
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/other",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urllib.request.urlopen(req)  # noqa: S310
        assert exc_info.value.code == _HTTP_NOT_FOUND
    finally:
        server.shutdown()
