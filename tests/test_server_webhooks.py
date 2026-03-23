"""Tests for the POST /webhooks/plaid endpoint and background sync wiring."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http
import logging
from typing import TYPE_CHECKING
from unittest.mock import ANY, MagicMock, patch

from fastapi.testclient import TestClient

from claw_plaid_ledger.config import OpenClawConfig
from claw_plaid_ledger.items_config import ItemConfig
from claw_plaid_ledger.server import _background_sync, app
from claw_plaid_ledger.sync_engine import SyncSummary

if TYPE_CHECKING:
    import pathlib

    import pytest

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105
_WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105
_OC_TOKEN = "test-oc-token"  # noqa: S105
_OC_URL = "http://127.0.0.1:18789/hooks/agent"


def _make_plaid_sig(body: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    """Compute a valid Plaid-Verification HMAC-SHA256 hex digest."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _make_mock_config(tmp_path: pathlib.Path) -> MagicMock:
    """Return a minimal mock Config for _background_sync tests."""
    mock_config = MagicMock()
    mock_config.plaid_access_token = "access-token"  # noqa: S105
    mock_config.item_id = "default-item"
    mock_config.db_path = tmp_path
    mock_config.openclaw_hooks_url = _OC_URL
    mock_config.openclaw_hooks_token = _OC_TOKEN
    mock_config.openclaw_hooks_agent = "Hestia"
    mock_config.openclaw_hooks_wake_mode = "now"
    return mock_config


class TestWebhookPlaid:
    """Tests for the POST /webhooks/plaid endpoint."""

    def test_valid_sync_updates_available_returns_200_and_enqueues_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SYNC_UPDATES_AVAILABLE with valid sig returns 200; enqueues sync."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", mock_bg
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
        mock_bg.assert_called_once()

    def test_invalid_signature_returns_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tampered or missing signature returns 400."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'

        response = client.post(
            "/webhooks/plaid",
            content=body,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Plaid-Verification": "bad-signature",
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == http.HTTPStatus.BAD_REQUEST

    def test_unknown_webhook_type_returns_200_without_enqueuing_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unrecognized webhook types are acknowledged with 200; no sync."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = b'{"webhook_type": "ITEM_WEBHOOK"}'
        sig = _make_plaid_sig(body)

        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", mock_bg
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
        mock_bg.assert_not_called()

    def test_sync_error_in_background_does_not_affect_response(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """A run_sync failure in the background does not change the 200."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        mock_config = MagicMock()
        # S105: value is a mock placeholder, not a real credential.
        mock_config.plaid_access_token = "access-token"  # noqa: S105
        mock_config.item_id = "default-item"
        mock_config.db_path = tmp_path
        mock_config.webhook_allowed_ips = []  # no IP filtering

        with (
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch(
                "claw_plaid_ledger.server.PlaidClientAdapter"
            ) as mock_adapter_cls,
            patch(
                "claw_plaid_ledger.server.run_sync",
                side_effect=RuntimeError("sync blew up"),
            ),
        ):
            mock_adapter_cls.from_config.return_value = MagicMock()

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


class TestWebhookItemRouting:
    """Tests for item_id-based routing in POST /webhooks/plaid."""

    def test_item_id_found_routes_to_configured_access_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """item_id matching a configured item routes to that item's token."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_ALICE", "access-token-alice")

        body = (
            b'{"webhook_type": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "bank-alice"}'
        )
        sig = _make_plaid_sig(body)

        item = ItemConfig(
            id="bank-alice",
            # S106: value is an env var name, not a credential
            access_token_env="PLAID_ACCESS_TOKEN_ALICE",  # noqa: S106
            owner="alice",
        )
        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", mock_bg
        )

        with patch(
            "claw_plaid_ledger.server.load_items_config", return_value=[item]
        ):
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
        mock_bg.assert_called_once_with(
            # S106: test fixture value, not a real credential
            access_token="access-token-alice",  # noqa: S106
            item_id="bank-alice",
            owner="alice",
            sync_run_id=ANY,
        )

    def test_item_id_not_in_items_toml_logs_warning_and_falls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """item_id absent from items.toml logs WARNING and falls back."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = (
            b'{"webhook_type": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "unknown-item"}'
        )
        sig = _make_plaid_sig(body)

        item = ItemConfig(
            id="bank-alice",
            # S106: value is an env var name, not a credential
            access_token_env="PLAID_ACCESS_TOKEN_ALICE",  # noqa: S106
        )
        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", mock_bg
        )

        with (
            caplog.at_level(
                logging.WARNING, logger="claw_plaid_ledger.server"
            ),
            patch(
                "claw_plaid_ledger.server.load_items_config",
                return_value=[item],
            ),
        ):
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
        mock_bg.assert_called_once_with(sync_run_id=ANY)
        warning_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        assert any("not found in items.toml" in m for m in warning_messages)

    def test_no_items_toml_falls_back_to_legacy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty items list (no items.toml) falls back to legacy sync."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = (
            b'{"webhook_type": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "bank-alice"}'
        )
        sig = _make_plaid_sig(body)

        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", mock_bg
        )

        with patch(
            "claw_plaid_ledger.server.load_items_config", return_value=[]
        ):
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
        mock_bg.assert_called_once_with(sync_run_id=ANY)

    def test_no_item_id_in_payload_falls_back_without_consulting_items_toml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Payload without item_id falls back; items.toml is not consulted."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", mock_bg
        )

        with patch("claw_plaid_ledger.server.load_items_config") as mock_load:
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
        mock_bg.assert_called_once_with(sync_run_id=ANY)
        mock_load.assert_not_called()

    def test_env_var_not_set_for_item_logs_error_and_falls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Missing access-token env var logs ERROR and falls back to legacy."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.delenv("PLAID_ACCESS_TOKEN_ALICE", raising=False)

        body = (
            b'{"webhook_type": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "bank-alice"}'
        )
        sig = _make_plaid_sig(body)

        item = ItemConfig(
            id="bank-alice",
            # S106: value is an env var name, not a credential
            access_token_env="PLAID_ACCESS_TOKEN_ALICE",  # noqa: S106
        )
        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", mock_bg
        )

        with (
            caplog.at_level(logging.ERROR, logger="claw_plaid_ledger.server"),
            patch(
                "claw_plaid_ledger.server.load_items_config",
                return_value=[item],
            ),
        ):
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
        mock_bg.assert_called_once_with(sync_run_id=ANY)
        error_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.ERROR
        ]
        assert any("not set" in m for m in error_messages)


# ---------------------------------------------------------------------------
# Tests for notify_openclaw wiring in _background_sync
# ---------------------------------------------------------------------------


class TestBackgroundSyncNotificationWiring:
    """Tests that _background_sync wires notify_openclaw correctly."""

    def test_notify_called_when_sync_has_changes(
        self, tmp_path: pathlib.Path
    ) -> None:
        """notify_openclaw called once when sync has non-zero changes."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=3, modified=1, removed=0, accounts=1, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.server.PlaidClientAdapter"),
            patch("claw_plaid_ledger.server.run_sync", return_value=summary),
            patch("claw_plaid_ledger.server.notify_openclaw") as mock_notify,
        ):
            asyncio.run(_background_sync())

        expected_openclaw_cfg = OpenClawConfig(
            url=mock_config.openclaw_hooks_url,
            token=mock_config.openclaw_hooks_token,
            agent=mock_config.openclaw_hooks_agent,
            wake_mode=mock_config.openclaw_hooks_wake_mode,
        )
        mock_notify.assert_called_once_with(summary, expected_openclaw_cfg)

    def test_notify_not_called_when_sync_has_no_changes(
        self, tmp_path: pathlib.Path
    ) -> None:
        """notify_openclaw not called when sync returns zero changes."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=0, modified=0, removed=0, accounts=0, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.server.PlaidClientAdapter"),
            patch("claw_plaid_ledger.server.run_sync", return_value=summary),
            patch("claw_plaid_ledger.server.notify_openclaw") as mock_notify,
        ):
            asyncio.run(_background_sync())

        mock_notify.assert_not_called()

    def test_notify_exception_does_not_propagate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Exception from notify_openclaw is caught; sync does not crash."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=1, modified=0, removed=0, accounts=1, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.server.PlaidClientAdapter"),
            patch("claw_plaid_ledger.server.run_sync", return_value=summary),
            patch(
                "claw_plaid_ledger.server.notify_openclaw",
                side_effect=RuntimeError("notifier bug"),
            ),
        ):
            # Must not raise — except Exception in _background_sync absorbs it.
            asyncio.run(_background_sync())


# ---------------------------------------------------------------------------
# Tests for _background_sync with injected credentials
# ---------------------------------------------------------------------------


class TestBackgroundSyncInjectedCredentials:
    """Tests for _background_sync() with explicit access_token / item_id."""

    def test_injected_access_token_is_passed_to_run_sync(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Injected access_token and item_id are forwarded to run_sync."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=0, modified=0, removed=0, accounts=0, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.server.PlaidClientAdapter"),
            patch(
                "claw_plaid_ledger.server.run_sync", return_value=summary
            ) as mock_run_sync,
        ):
            asyncio.run(
                _background_sync(
                    # S106: test fixture value, not a real credential
                    access_token="injected-token",  # noqa: S106
                    item_id="bank-alice",
                    owner="alice",
                )
            )

        mock_run_sync.assert_called_once()
        call_kwargs = mock_run_sync.call_args.kwargs
        # S105: comparing against a test fixture token, not a real credential
        assert call_kwargs["access_token"] == "injected-token"  # noqa: S105
        assert call_kwargs["item_id"] == "bank-alice"
        assert call_kwargs["owner"] == "alice"

    def test_no_args_uses_config_values_backward_compat(
        self, tmp_path: pathlib.Path
    ) -> None:
        """_background_sync() with no args uses config token and item_id."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=0, modified=0, removed=0, accounts=0, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.server.PlaidClientAdapter"),
            patch(
                "claw_plaid_ledger.server.run_sync", return_value=summary
            ) as mock_run_sync,
        ):
            asyncio.run(_background_sync())

        mock_run_sync.assert_called_once()
        call_kwargs = mock_run_sync.call_args.kwargs
        assert call_kwargs["access_token"] == mock_config.plaid_access_token
        assert call_kwargs["item_id"] == mock_config.item_id
