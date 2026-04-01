"""Tests for claw_plaid_ledger.notifier."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    import pytest

from claw_plaid_ledger.config import OpenClawConfig
from claw_plaid_ledger.notifier import notify_openclaw
from claw_plaid_ledger.sync_engine import SyncSummary

_URL = "http://127.0.0.1:18789/hooks/agent"
_TOKEN = "test-token"  # noqa: S105
_AGENT = "Hestia"
_WAKE_MODE = "now"


def _summary(
    added: int = 1, modified: int = 0, removed: int = 0
) -> SyncSummary:
    """Return a SyncSummary with the given change counts."""
    return SyncSummary(
        added=added,
        modified=modified,
        removed=removed,
        accounts=1,
        next_cursor="cur",
    )


def _config(
    *,
    url: str = _URL,
    token: str | None = _TOKEN,
    agent: str = _AGENT,
    wake_mode: str = _WAKE_MODE,
) -> OpenClawConfig:
    """Return an OpenClawConfig with the given values."""
    return OpenClawConfig(
        url=url, token=token, agent=agent, wake_mode=wake_mode
    )


def _make_mock_resp(status: int = 200) -> MagicMock:
    """Return a context-manager mock response with the given status."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestTokenGuard:
    """Tests for the token guard at the top of notify_openclaw."""

    def test_none_token_logs_warning_and_skips_urlopen(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """token=None must log a warning and never call urlopen."""
        with (
            patch("urllib.request.urlopen") as mock_urlopen,
            caplog.at_level("WARNING"),
        ):
            notify_openclaw(_summary(), _config(token=None))
        mock_urlopen.assert_not_called()
        assert "OPENCLAW_HOOKS_TOKEN not set" in caplog.text

    def test_empty_string_token_logs_warning_and_skips_urlopen(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """token='' must be treated the same as None."""
        with (
            patch("urllib.request.urlopen") as mock_urlopen,
            caplog.at_level("WARNING"),
        ):
            notify_openclaw(_summary(), _config(token=""))
        mock_urlopen.assert_not_called()
        assert "OPENCLAW_HOOKS_TOKEN not set" in caplog.text


class TestUrlSchemeGuard:
    """Tests for the URL scheme validation guard."""

    def test_file_scheme_logs_warning_and_skips_urlopen(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A file: URL must be rejected with a warning."""
        with (
            patch("urllib.request.urlopen") as mock_urlopen,
            caplog.at_level("WARNING"),
        ):
            notify_openclaw(_summary(), _config(url="file:///etc/passwd"))
        mock_urlopen.assert_not_called()
        assert "unsupported scheme" in caplog.text

    def test_custom_scheme_logs_warning_and_skips_urlopen(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A custom scheme must be rejected with a warning."""
        with (
            patch("urllib.request.urlopen") as mock_urlopen,
            caplog.at_level("WARNING"),
        ):
            notify_openclaw(_summary(), _config(url="ftp://example.com/hook"))
        mock_urlopen.assert_not_called()
        assert "unsupported scheme" in caplog.text

    def test_https_scheme_is_allowed(self) -> None:
        """https: is an allowed scheme and must proceed to urlopen."""
        with patch(
            "urllib.request.urlopen", return_value=_make_mock_resp()
        ) as mock_urlopen:
            notify_openclaw(
                _summary(),
                _config(url="https://example.com/hooks/agent"),
            )
        mock_urlopen.assert_called_once()


class TestMessageConstruction:
    """Tests for the human-readable message assembled from change counts."""

    def _capture_request(
        self, added: int, modified: int, removed: int
    ) -> urllib.request.Request:
        """Run notify_openclaw and return the Request passed to urlopen."""
        captured: list[urllib.request.Request] = []

        def fake_urlopen(
            req: urllib.request.Request, **_kwargs: object
        ) -> MagicMock:
            """Capture the request and return a mock response."""
            captured.append(req)
            return _make_mock_resp()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            notify_openclaw(
                _summary(added=added, modified=modified, removed=removed),
                _config(),
            )

        assert len(captured) == 1
        return captured[0]

    def test_added_and_modified(self) -> None:
        """Non-zero added and modified both appear in the message."""
        req = self._capture_request(added=3, modified=1, removed=0)
        body = json.loads(req.data)  # type: ignore[arg-type]
        assert body["message"] == (
            "Plaid sync complete: 3 added, 1 modified."
            " Hestia should run ingestion allocation updates; Athena reviews"
            " later on schedule or anomaly flags."
        )

    def test_added_only(self) -> None:
        """Only the added count appears when modified and removed are zero."""
        req = self._capture_request(added=5, modified=0, removed=0)
        body = json.loads(req.data)  # type: ignore[arg-type]
        assert body["message"] == (
            "Plaid sync complete: 5 added."
            " Hestia should run ingestion allocation updates; Athena reviews"
            " later on schedule or anomaly flags."
        )

    def test_removed_only(self) -> None:
        """Only the removed count appears when added and modified are zero."""
        req = self._capture_request(added=0, modified=0, removed=2)
        body = json.loads(req.data)  # type: ignore[arg-type]
        assert body["message"] == (
            "Plaid sync complete: 2 removed."
            " Hestia should run ingestion allocation updates; Athena reviews"
            " later on schedule or anomaly flags."
        )


class TestSuccessfulPost:
    """Tests for a successful HTTP POST to the OpenClaw endpoint."""

    def test_urlopen_called_once_with_correct_request(self) -> None:
        """One POST is sent with the correct method, headers, and body."""
        with patch(
            "urllib.request.urlopen", return_value=_make_mock_resp()
        ) as mock_open:
            notify_openclaw(_summary(), _config())

        mock_open.assert_called_once()
        req: urllib.request.Request = mock_open.call_args[0][0]

        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"
        assert req.get_header("Authorization") == f"Bearer {_TOKEN}"

        body = json.loads(req.data)  # type: ignore[arg-type]
        assert body["name"] == _AGENT
        assert body["wakeMode"] == _WAKE_MODE

    def test_success_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """A 200 response results in an INFO log line."""
        with (
            patch("urllib.request.urlopen", return_value=_make_mock_resp()),
            caplog.at_level("INFO"),
        ):
            notify_openclaw(_summary(), _config())

        assert "OpenClaw notification sent" in caplog.text


class TestErrorHandling:
    """Network and HTTP errors are caught and logged, never raised."""

    def test_url_error_logs_warning_and_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A URLError must be caught, logged at WARNING, and not re-raised."""
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection refused"),
            ),
            caplog.at_level("WARNING"),
        ):
            notify_openclaw(_summary(), _config())

        assert "OpenClaw notification failed (network)" in caplog.text

    def test_http_error_401_logs_warning_and_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An HTTP 401 must be caught, logged at WARNING, and not re-raised."""
        http_err = urllib.error.HTTPError(
            url=_URL,
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )
        with (
            patch("urllib.request.urlopen", side_effect=http_err),
            caplog.at_level("WARNING"),
        ):
            notify_openclaw(_summary(), _config())

        assert "OpenClaw notification failed: HTTP 401" in caplog.text
