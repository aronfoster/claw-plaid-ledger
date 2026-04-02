"""Tests for the POST /webhooks/plaid endpoint and IP allowlist middleware."""

from __future__ import annotations

import hashlib
import hmac
import http
import logging
from typing import TYPE_CHECKING
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from claw_plaid_ledger.items_config import ItemConfig
from claw_plaid_ledger.server import app

if TYPE_CHECKING:
    import pathlib

    import pytest

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105
_WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105

# Canonical SYNC_UPDATES_AVAILABLE payload — matches the real Plaid shape
# (webhook_type="TRANSACTIONS", webhook_code="SYNC_UPDATES_AVAILABLE").
_SYNC_BODY = (
    b'{"webhook_type": "TRANSACTIONS",'
    b' "webhook_code": "SYNC_UPDATES_AVAILABLE"}'
)


def _make_plaid_sig(body: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    """Compute a valid Plaid-Verification HMAC-SHA256 hex digest."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestWebhookPlaid:
    """Tests for the POST /webhooks/plaid endpoint."""

    def test_valid_sync_updates_available_returns_200_and_enqueues_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SYNC_UPDATES_AVAILABLE with valid sig returns 200; enqueues sync."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
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

        body = _SYNC_BODY

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
            "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
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

    def test_malformed_json_body_returns_400_and_logs_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Malformed JSON body returns 400 and logs an ERROR."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = b"not-valid-json{"
        sig = _make_plaid_sig(body)

        with caplog.at_level(
            logging.ERROR, logger="claw_plaid_ledger.routers.webhooks"
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

        assert response.status_code == http.HTTPStatus.BAD_REQUEST
        assert any("not valid JSON" in r.message for r in caplog.records)

    def test_sync_error_in_background_does_not_affect_response(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """A run_sync failure in the background does not change the 200."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        mock_config = MagicMock()
        # S105: value is a mock placeholder, not a real credential.
        mock_config.plaid_access_token = "access-token"  # noqa: S105
        mock_config.item_id = "default-item"
        mock_config.db_path = tmp_path
        mock_config.webhook_allowed_ips = []  # no IP filtering

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"
            ) as mock_adapter_cls,
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Plaid item_id resolved via sync_state routes to item's token."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_ALICE", "access-token-alice")
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )

        plaid_item_id = "M0RJm3p05Qhkow14o1azcgog1rKNvAfdwBq8q"
        body = (
            b'{"webhook_type": "TRANSACTIONS",'
            b' "webhook_code": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "' + plaid_item_id.encode() + b'"}'
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
            "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
        )

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[item],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._resolve_logical_item_id",
                return_value="bank-alice",
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
        mock_bg.assert_called_once_with(
            # S106: test fixture value, not a real credential
            access_token="access-token-alice",  # noqa: S106
            item_id="bank-alice",
            owner="alice",
            sync_run_id=ANY,
        )

    def test_plaid_item_id_not_in_sync_state_logs_warning_and_skips(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: pathlib.Path,
    ) -> None:
        """Plaid item_id not in sync_state logs WARNING and skips sync."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )

        body = (
            b'{"webhook_type": "TRANSACTIONS",'
            b' "webhook_code": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "unknown-plaid-item-id"}'
        )
        sig = _make_plaid_sig(body)

        item = ItemConfig(
            id="bank-alice",
            # S106: value is an env var name, not a credential
            access_token_env="PLAID_ACCESS_TOKEN_ALICE",  # noqa: S106
        )
        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
        )

        with (
            caplog.at_level(
                logging.WARNING, logger="claw_plaid_ledger.routers.webhooks"
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[item],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._resolve_logical_item_id",
                return_value=None,
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
        mock_bg.assert_not_called()
        warning_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        assert any("not in sync_state" in m for m in warning_messages)

    def test_no_items_toml_falls_back_to_legacy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty items list (no items.toml) falls back to legacy sync."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = (
            b'{"webhook_type": "TRANSACTIONS",'
            b' "webhook_code": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "bank-alice"}'
        )
        sig = _make_plaid_sig(body)

        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
        )

        with patch(
            "claw_plaid_ledger.routers.webhooks.load_items_config",
            return_value=[],
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

    def test_no_item_id_in_payload_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Payload without item_id logs WARNING before falling back."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync",
            MagicMock(),
        )

        with caplog.at_level(
            logging.WARNING, logger="claw_plaid_ledger.routers.webhooks"
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

        assert any(
            "no item_id in payload" in r.message for r in caplog.records
        )

    def test_no_item_id_in_payload_falls_back_without_consulting_items_toml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Payload without item_id falls back; items.toml is not consulted."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
        )

        with patch(
            "claw_plaid_ledger.routers.webhooks.load_items_config"
        ) as mock_load:
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

    def test_env_var_not_set_for_item_logs_error_and_skips(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: pathlib.Path,
    ) -> None:
        """Missing access-token env var logs ERROR and skips sync."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        monkeypatch.setenv("PLAID_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "test.db")
        )
        monkeypatch.delenv("PLAID_ACCESS_TOKEN_ALICE", raising=False)

        plaid_item_id = "M0RJm3p05Qhkow14o1azcgog1rKNvAfdwBq8q"
        body = (
            b'{"webhook_type": "TRANSACTIONS",'
            b' "webhook_code": "SYNC_UPDATES_AVAILABLE",'
            b' "item_id": "' + plaid_item_id.encode() + b'"}'
        )
        sig = _make_plaid_sig(body)

        item = ItemConfig(
            id="bank-alice",
            # S106: value is an env var name, not a credential
            access_token_env="PLAID_ACCESS_TOKEN_ALICE",  # noqa: S106
        )
        mock_bg = MagicMock()
        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
        )

        with (
            caplog.at_level(
                logging.ERROR, logger="claw_plaid_ledger.routers.webhooks"
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[item],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._resolve_logical_item_id",
                return_value="bank-alice",
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
        mock_bg.assert_not_called()
        error_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.ERROR
        ]
        assert any("not set" in m for m in error_messages)


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

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", AsyncMock()
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

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", AsyncMock()
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

        body = _SYNC_BODY
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

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        monkeypatch.setattr(
            "claw_plaid_ledger.routers.webhooks._background_sync", AsyncMock()
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

        body = _SYNC_BODY
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

        body = _SYNC_BODY
        sig = _make_plaid_sig(body)

        with caplog.at_level(
            logging.WARNING, logger="claw_plaid_ledger.routers.webhooks"
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
