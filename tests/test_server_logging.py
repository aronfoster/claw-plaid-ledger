"""Tests for structured logging, correlation IDs, and sync_run_id."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from claw_plaid_ledger.db import initialize_database
from claw_plaid_ledger.routers.webhooks import _background_sync
from claw_plaid_ledger.server import app
from claw_plaid_ledger.sync_engine import SyncSummary

if TYPE_CHECKING:
    import pathlib

    import pytest

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105
_WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105

_REQUEST_ID_LENGTH = 12  # "req-" (4) + 8 hex chars

# Real Plaid SYNC_UPDATES_AVAILABLE payload shape.
_SYNC_BODY = (
    b'{"webhook_type": "TRANSACTIONS",'
    b' "webhook_code": "SYNC_UPDATES_AVAILABLE"}'
)


def _make_plaid_sig(body: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    """Compute a valid Plaid-Verification HMAC-SHA256 hex digest."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Structured logging tests
# ---------------------------------------------------------------------------


class TestStructuredLogging:
    """Tests for INFO/ERROR log coverage in the webhook handler."""

    def test_invalid_signature_logs_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An invalid Plaid signature logs an ERROR before returning 400."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = _SYNC_BODY

        with caplog.at_level(
            logging.ERROR, logger="claw_plaid_ledger.routers.webhooks"
        ):
            client.post(
                "/webhooks/plaid",
                content=body,
                headers={
                    "Authorization": f"Bearer {_TOKEN}",
                    "Plaid-Verification": "tampered-bad-sig",
                    "Content-Type": "application/json",
                },
            )

        error_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.ERROR
        ]
        assert any("signature" in m.lower() for m in error_messages), (
            f"Expected ERROR log mentioning signature; got: {error_messages}"
        )

    def test_background_sync_exception_logs_error_with_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: pathlib.Path,
    ) -> None:
        """A run_sync exception is logged as ERROR with exc_info."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        mock_config = MagicMock()
        mock_config.plaid_access_token = "access-token"  # noqa: S105
        mock_config.item_id = "default-item"
        mock_config.db_path = tmp_path
        mock_config.webhook_allowed_ips = []  # no IP filtering

        with (
            caplog.at_level(
                logging.ERROR, logger="claw_plaid_ledger.routers.webhooks"
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"
            ) as mock_adapter_cls,
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                side_effect=RuntimeError("deliberate test failure"),
            ),
        ):
            mock_adapter_cls.from_config.return_value = MagicMock()
            client.post(
                "/webhooks/plaid",
                content=body,
                headers={
                    "Authorization": f"Bearer {_TOKEN}",
                    "Plaid-Verification": sig,
                    "Content-Type": "application/json",
                },
            )

        error_records = [
            r for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert error_records, "Expected at least one ERROR log record"
        # exc_info should be set so the traceback is captured
        assert any(r.exc_info is not None for r in error_records), (
            "Expected ERROR record to include exc_info (traceback)"
        )


# ---------------------------------------------------------------------------
# Tests for X-Request-Id correlation ID middleware
# ---------------------------------------------------------------------------


class TestCorrelationIdMiddleware:
    """Tests for X-Request-Id header injection and unique ID generation."""

    def test_health_response_includes_x_request_id(self) -> None:
        """Every response includes an X-Request-Id header."""
        response = client.get("/health")
        assert "x-request-id" in response.headers

    def test_x_request_id_has_req_prefix(self) -> None:
        """The X-Request-Id value starts with 'req-'."""
        response = client.get("/health")
        assert response.headers["x-request-id"].startswith("req-")

    def test_x_request_id_has_correct_length(self) -> None:
        """The X-Request-Id suffix is 8 hex characters (total length 12)."""
        response = client.get("/health")
        assert len(response.headers["x-request-id"]) == _REQUEST_ID_LENGTH

    def test_each_request_gets_unique_id(self) -> None:
        """Two consecutive requests receive different X-Request-Id values."""
        response1 = client.get("/health")
        response2 = client.get("/health")
        id1 = response1.headers["x-request-id"]
        id2 = response2.headers["x-request-id"]
        assert id1 != id2

    def test_authenticated_endpoint_includes_x_request_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Protected endpoints also carry the X-Request-Id header."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert "x-request-id" in response.headers
        assert response.headers["x-request-id"].startswith("req-")

    def test_middleware_logs_request_start_and_end(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Middleware emits INFO logs for request_start and request_end."""
        with caplog.at_level(
            logging.INFO, logger="claw_plaid_ledger.middleware.correlation"
        ):
            client.get("/health")

        messages = [r.getMessage() for r in caplog.records]
        assert any("request_start" in m for m in messages)
        assert any("request_end" in m for m in messages)


# ---------------------------------------------------------------------------
# Tests for sync_run_id propagation
# ---------------------------------------------------------------------------


class TestSyncRunId:
    """Tests for sync_run_id presence in log output."""

    def test_background_sync_logs_sync_run_id(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path: pathlib.Path,
    ) -> None:
        """_background_sync logs lines that include the sync_run_id."""
        mock_config = MagicMock()
        mock_config.plaid_access_token = "access-token"  # noqa: S105
        mock_config.item_id = "default-item"
        mock_config.db_path = tmp_path / "db.sqlite"
        mock_config.openclaw_hooks_token = None

        mock_summary = SyncSummary(
            added=1, modified=0, removed=0, accounts=1, next_cursor="cur"
        )

        with (
            caplog.at_level(
                logging.INFO, logger="claw_plaid_ledger.routers.webhooks"
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"
            ) as mock_cls,
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                return_value=mock_summary,
            ),
        ):
            mock_cls.from_config.return_value = MagicMock()
            asyncio.run(
                _background_sync(
                    access_token="tok",  # noqa: S106
                    item_id="default-item",
                    sync_run_id="sync-testid1",
                )
            )

        messages = [r.getMessage() for r in caplog.records]
        assert any("sync-testid1" in m for m in messages), (
            "Expected sync_run_id 'sync-testid1' in log messages;"
            f" got: {messages}"
        )

    def test_background_sync_generates_sync_run_id_when_none_given(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path: pathlib.Path,
    ) -> None:
        """_background_sync auto-generates sync_run_id when not provided."""
        mock_config = MagicMock()
        mock_config.plaid_access_token = "access-token"  # noqa: S105
        mock_config.item_id = "default-item"
        mock_config.db_path = tmp_path / "db.sqlite"
        mock_config.openclaw_hooks_token = None

        mock_summary = SyncSummary(
            added=0, modified=0, removed=0, accounts=0, next_cursor="cur"
        )

        with (
            caplog.at_level(
                logging.INFO, logger="claw_plaid_ledger.routers.webhooks"
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"
            ) as mock_cls,
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                return_value=mock_summary,
            ),
        ):
            mock_cls.from_config.return_value = MagicMock()
            asyncio.run(_background_sync(access_token="tok"))  # noqa: S106

        messages = [r.getMessage() for r in caplog.records]
        # An auto-generated ID starts with "sync-"
        assert any("sync-" in m for m in messages), (
            "Expected auto-generated sync_run_id in log messages;"
            f" got: {messages}"
        )

    def test_webhook_passes_sync_run_id_to_background_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Webhook handler passes a sync_run_id derived from request_id."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        captured_sync_run_id: list[str] = []

        async def _capture_sync(*, sync_run_id: str | None = None) -> None:
            if sync_run_id is not None:
                captured_sync_run_id.append(sync_run_id)

        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync",
            _capture_sync,
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

        assert response.status_code == http.HTTPStatus.OK
        assert captured_sync_run_id, "Expected sync_run_id to be passed"
        assert captured_sync_run_id[0].startswith("sync-")
