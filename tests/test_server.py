"""Tests for the FastAPI server module."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http
import ipaddress
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import fastapi
import pytest
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from claw_plaid_ledger.config import Config, OpenClawConfig
from claw_plaid_ledger.db import (
    get_annotation,
    initialize_database,
    upsert_sync_state,
)
from claw_plaid_ledger.items_config import ItemConfig
from claw_plaid_ledger.server import (
    _background_sync,
    _check_and_sync_overdue_items,
    _ip_in_allowlist,
    _resolve_client_ip,
    _scheduled_sync_loop,
    app,
    lifespan,
    require_bearer_token,
)
from claw_plaid_ledger.sync_engine import SyncSummary

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Generator
    from typing import Any

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105


# ---------------------------------------------------------------------------
# Tests for the public /health endpoint (no auth required)
# ---------------------------------------------------------------------------


def test_health_returns_200() -> None:
    """`GET /health` responds with HTTP 200."""
    response = client.get("/health")
    assert response.status_code == http.HTTPStatus.OK


def test_health_returns_ok_payload() -> None:
    """`GET /health` body contains status ok."""
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


def test_health_no_auth_required() -> None:
    """`GET /health` succeeds without any Authorization header."""
    response = client.get("/health")
    assert response.status_code == http.HTTPStatus.OK


# ---------------------------------------------------------------------------
# Unit tests for the require_bearer_token dependency
# ---------------------------------------------------------------------------


class TestRequireBearerToken:
    """Direct unit tests for the require_bearer_token dependency function."""

    def test_missing_credentials_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_bearer_token raises 401 when credentials are None."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        with pytest.raises(HTTPException) as exc_info:
            require_bearer_token(None)
        assert exc_info.value.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_wrong_token_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_bearer_token raises 401 when the token is wrong."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="wrong-value"
        )
        with pytest.raises(HTTPException) as exc_info:
            require_bearer_token(creds)
        assert exc_info.value.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_correct_token_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_bearer_token does not raise when the token is correct."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=_TOKEN
        )
        require_bearer_token(creds)  # must not raise

    def test_no_secret_configured_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_bearer_token raises 401 when CLAW_API_SECRET is unset."""
        monkeypatch.delenv("CLAW_API_SECRET", raising=False)
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=_TOKEN
        )
        with pytest.raises(HTTPException) as exc_info:
            require_bearer_token(creds)
        assert exc_info.value.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_secret_from_env_file_authenticates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        require_bearer_token accepts secret resolved from .env file.

        Regression test for BUG-003: auth must use load_api_secret() (which
        merges the .env file with process env) rather than reading
        os.environ directly.
        """
        monkeypatch.delenv("CLAW_API_SECRET", raising=False)
        # Simulate load_api_secret() returning the secret from the .env file
        # (i.e. not present in the process environment).
        with patch(
            "claw_plaid_ledger.server.load_api_secret", return_value=_TOKEN
        ):
            creds = HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=_TOKEN
            )
            require_bearer_token(creds)  # must not raise


# ---------------------------------------------------------------------------
# Integration tests via a protected test endpoint
# ---------------------------------------------------------------------------

_protected_app = fastapi.FastAPI()


@_protected_app.get("/health")
def _health() -> dict[str, str]:
    return {"status": "ok"}


@_protected_app.get("/protected", dependencies=[Depends(require_bearer_token)])
def _protected() -> dict[str, str]:
    return {"ok": "true"}


_protected_client = TestClient(_protected_app)


class TestProtectedRoute:
    """Integration tests for bearer auth on a protected route."""

    def test_missing_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requests without Authorization header return 401."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        response = _protected_client.get("/protected")
        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_wrong_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requests with an incorrect token return 401."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        response = _protected_client.get(
            "/protected",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_correct_token_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requests with the correct token reach the route handler."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        response = _protected_client.get(
            "/protected",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK


# ---------------------------------------------------------------------------
# Tests for POST /webhooks/plaid
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105


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

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'

        with caplog.at_level(logging.ERROR, logger="claw_plaid_ledger.server"):
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

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        mock_config = MagicMock()
        mock_config.plaid_access_token = "access-token"  # noqa: S105
        mock_config.item_id = "default-item"
        mock_config.db_path = tmp_path
        mock_config.webhook_allowed_ips = []  # no IP filtering

        with (
            caplog.at_level(logging.ERROR, logger="claw_plaid_ledger.server"),
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch(
                "claw_plaid_ledger.server.PlaidClientAdapter"
            ) as mock_adapter_cls,
            patch(
                "claw_plaid_ledger.server.run_sync",
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


def _seed_transactions(db_path: pathlib.Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            (
                "INSERT INTO accounts ("
                "plaid_account_id, name, created_at, updated_at"
                ") VALUES (?, ?, ?, ?)"
            ),
            [
                (
                    "acct_1",
                    "Account 1",
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "acct_2",
                    "Account 2",
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.executemany(
            (
                "INSERT INTO transactions ("
                "plaid_transaction_id, plaid_account_id, amount, "
                "iso_currency_code, name, merchant_name, pending, "
                "authorized_date, posted_date, raw_json, created_at, "
                "updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_1",
                    "acct_1",
                    12.34,
                    "USD",
                    "Starbucks",
                    "Starbucks",
                    0,
                    None,
                    "2024-01-15",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "tx_2",
                    "acct_2",
                    55.0,
                    "USD",
                    "GROCERY",
                    "Whole Foods",
                    1,
                    "2024-01-20",
                    None,
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )


class TestListTransactionsEndpoint:
    """Tests for GET /transactions endpoint behavior."""

    def test_requires_bearer_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Missing Authorization header returns 401."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get("/transactions")

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_invalid_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Wrong bearer token returns 401."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_list_transactions_supports_filters_and_pagination(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Filtering and pagination return expected rows and totals."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={
                "start_date": "2024-01-10",
                "end_date": "2024-01-31",
                "account_id": "acct_1",
                "pending": "false",
                "min_amount": "10",
                "max_amount": "20",
                "keyword": "star",
                "limit": "10",
                "offset": "0",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {
            "transactions": [
                {
                    "id": "tx_1",
                    "account_id": "acct_1",
                    "amount": 12.34,
                    "iso_currency_code": "USD",
                    "name": "Starbucks",
                    "merchant_name": "Starbucks",
                    "pending": False,
                    "date": "2024-01-15",
                }
            ],
            "total": 1,
            "limit": 10,
            "offset": 0,
        }

        page_response = client.get(
            "/transactions",
            params={"limit": "1", "offset": "1"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert page_response.status_code == http.HTTPStatus.OK
        expected_total = 2
        assert page_response.json()["total"] == expected_total
        assert page_response.json()["transactions"][0]["id"] == "tx_1"

    def test_default_view_is_canonical_and_view_raw_opt_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Canonical view hides suppressed-account transactions by default."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                (
                    "UPDATE accounts SET canonical_account_id = ? "
                    "WHERE plaid_account_id = ?"
                ),
                ("acct_1", "acct_2"),
            )
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        canonical_response = client.get(
            "/transactions", headers={"Authorization": f"Bearer {_TOKEN}"}
        )
        raw_response = client.get(
            "/transactions",
            params={"view": "raw"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert canonical_response.status_code == http.HTTPStatus.OK
        assert canonical_response.json()["total"] == 1
        assert canonical_response.json()["transactions"][0]["id"] == "tx_1"

        assert raw_response.status_code == http.HTTPStatus.OK
        expected_raw_total = 2
        assert raw_response.json()["total"] == expected_raw_total

    def test_invalid_view_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unsupported view query value is rejected with HTTP 422."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"view": "invalid"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_limit_above_max_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Limit over 500 is rejected by validation with 422."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"limit": "501"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_empty_db_returns_empty_envelope(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Empty database returns empty transaction list and total 0."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {
            "transactions": [],
            "total": 0,
            "limit": 100,
            "offset": 0,
        }


class TestGetTransactionDetailEndpoint:
    """Tests for GET /transactions/{transaction_id} endpoint behavior."""

    def test_requires_bearer_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Missing Authorization header returns 401."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get("/transactions/tx_1")

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_invalid_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Wrong bearer token returns 401."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_known_id_without_annotation_returns_null_annotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Known transaction without annotation returns annotation null."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {
            "id": "tx_1",
            "account_id": "acct_1",
            "amount": 12.34,
            "iso_currency_code": "USD",
            "name": "Starbucks",
            "merchant_name": "Starbucks",
            "pending": False,
            "date": "2024-01-15",
            "raw_json": None,
            "suppressed_by": None,
            "annotation": None,
        }

    def test_known_id_with_annotation_parses_tags_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Annotation tags are returned as parsed JSON list."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                (
                    "INSERT INTO annotations ("
                    "plaid_transaction_id, category, note, tags, "
                    "created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)"
                ),
                (
                    "tx_1",
                    "food",
                    "Morning coffee",
                    '["discretionary", "recurring"]',
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-02T00:00:00+00:00",
                ),
            )

        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json()["annotation"] == {
            "category": "food",
            "note": "Morning coffee",
            "tags": ["discretionary", "recurring"],
            "updated_at": "2024-01-02T00:00:00+00:00",
        }

    def test_annotation_with_null_tags_returns_null_tags(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Annotation with NULL tags returns tags as null."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                (
                    "INSERT INTO annotations ("
                    "plaid_transaction_id, category, note, tags, "
                    "created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)"
                ),
                (
                    "tx_1",
                    "food",
                    "Morning coffee",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-02T00:00:00+00:00",
                ),
            )

        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json()["annotation"]["tags"] is None

    def test_suppressed_transaction_includes_suppressed_by(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Detail response surfaces canonical account provenance."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                (
                    "UPDATE accounts SET canonical_account_id = ? "
                    "WHERE plaid_account_id = ?"
                ),
                ("acct_1", "acct_2"),
            )

        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions/tx_2",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json()["suppressed_by"] == "acct_1"

    def test_unknown_transaction_id_returns_404(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unknown transaction id returns 404."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions/missing",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.NOT_FOUND


class TestPutAnnotationEndpoint:
    """Tests for PUT /annotations/{transaction_id} endpoint behavior."""

    def test_put_returns_full_transaction_shape(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT returns HTTP 200 with full transaction shape, not status:ok."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/annotations/tx_1",
            json={
                "category": "food",
                "note": "Morning coffee",
                "tags": ["discretionary"],
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        # Must contain full transaction fields
        assert body["id"] == "tx_1"
        assert body["account_id"] == "acct_1"
        assert "amount" in body
        assert body["name"] == "Starbucks"
        assert "annotation" in body
        # Annotation block must reflect values just written
        annotation = body["annotation"]
        assert annotation is not None
        assert annotation["category"] == "food"
        assert annotation["note"] == "Morning coffee"
        assert annotation["tags"] == ["discretionary"]
        assert annotation["updated_at"] is not None

    def test_second_put_replaces_annotation_preserves_created_at(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Second PUT replaces; created_at unchanged, updated_at changes."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        client.put(
            "/annotations/tx_1",
            json={"category": "food", "note": "First note"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        with sqlite3.connect(db_path) as connection:
            first = get_annotation(connection, "tx_1")
        assert first is not None
        first_created_at = first.created_at
        first_updated_at = first.updated_at

        client.put(
            "/annotations/tx_1",
            json={"category": "transport", "note": "Second note"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        with sqlite3.connect(db_path) as connection:
            second = get_annotation(connection, "tx_1")
        assert second is not None
        assert second.created_at == first_created_at
        assert second.category == "transport"
        assert second.note == "Second note"
        # updated_at should be >= first_updated_at
        assert second.updated_at >= first_updated_at

    def test_second_put_response_reflects_updated_annotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Second PUT (update) returns the newly updated annotation fields."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        client.put(
            "/annotations/tx_1",
            json={"category": "food", "note": "First note", "tags": ["a"]},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        response = client.put(
            "/annotations/tx_1",
            json={
                "category": "transport",
                "note": "Updated note",
                "tags": ["b", "c"],
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        annotation = body["annotation"]
        assert annotation is not None
        assert annotation["category"] == "transport"
        assert annotation["note"] == "Updated note"
        assert annotation["tags"] == ["b", "c"]

    def test_put_empty_body_stores_all_null(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT with empty body stores all-null annotation; returns 200."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/annotations/tx_1",
            json={},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        with sqlite3.connect(db_path) as connection:
            annotation = get_annotation(connection, "tx_1")
        assert annotation is not None
        assert annotation.category is None
        assert annotation.note is None
        assert annotation.tags is None

    def test_put_with_empty_tags_list_round_trips(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT with tags=[] stores empty list and round-trips as []."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        client.put(
            "/annotations/tx_1",
            json={"tags": []},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json()["annotation"]["tags"] == []

    def test_put_unknown_transaction_returns_404(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT for unknown transaction_id returns 404."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/annotations/unknown_tx",
            json={"category": "food"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.NOT_FOUND

    def test_put_missing_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Missing Authorization header returns 401."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/annotations/tx_1",
            json={"category": "food"},
        )

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_put_wrong_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Wrong bearer token returns 401."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/annotations/tx_1",
            json={"category": "food"},
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_end_to_end_put_then_get_annotation_block_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT then GET; annotation block in detail response matches."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        client.put(
            "/annotations/tx_1",
            json={
                "category": "food",
                "note": "Morning coffee",
                "tags": ["discretionary", "recurring"],
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        annotation = response.json()["annotation"]
        assert annotation is not None
        assert annotation["category"] == "food"
        assert annotation["note"] == "Morning coffee"
        assert annotation["tags"] == ["discretionary", "recurring"]


# ---------------------------------------------------------------------------
# Tests for notify_openclaw wiring in _background_sync
# ---------------------------------------------------------------------------


_OC_TOKEN = "test-oc-token"  # noqa: S105
_OC_URL = "http://127.0.0.1:18789/hooks/agent"


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
# Tests for multi-item webhook routing
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests for lifespan context manager
# ---------------------------------------------------------------------------


class TestLifespan:
    """Tests for the FastAPI lifespan startup/shutdown behavior."""

    def test_disabled_no_task_created(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """No background task is created when scheduled sync is disabled."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_SCHEDULED_SYNC_ENABLED", "false")

        created_tasks: list[object] = []

        def _capture_task(coro: object) -> MagicMock:
            created_tasks.append(coro)
            return MagicMock()

        async def _run() -> None:
            with patch(
                "claw_plaid_ledger.server.asyncio.create_task",
                side_effect=_capture_task,
            ):
                async with lifespan(app):
                    pass

        asyncio.run(_run())
        assert created_tasks == []

    def test_enabled_task_created_and_cancelled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Enabled scheduled sync starts a task cancelled on shutdown."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_SCHEDULED_SYNC_ENABLED", "true")

        cancel_called: list[bool] = []

        class _FakeTask:
            """Minimal awaitable that raises CancelledError on await."""

            def cancel(self) -> None:
                cancel_called.append(True)

            def __await__(
                self,
            ) -> Generator[Any, None, None]:
                raise asyncio.CancelledError
                yield  # pragma: no cover  # makes this a generator function

        task_fake = _FakeTask()

        async def _run() -> None:
            with patch(
                "claw_plaid_ledger.server.asyncio.create_task",
                return_value=task_fake,
            ) as mock_create:
                async with lifespan(app):
                    pass

            mock_create.assert_called_once()
            assert cancel_called == [True]

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests for _check_and_sync_overdue_items
# ---------------------------------------------------------------------------


def _make_scheduled_sync_config(
    tmp_path: pathlib.Path,
    *,
    fallback_hours: int = 24,
) -> Config:
    """Build a minimal Config for scheduled-sync tests."""
    return Config(
        db_path=tmp_path / "db.sqlite",
        workspace_path=None,
        plaid_client_id=None,
        plaid_secret=None,
        plaid_env=None,
        plaid_access_token=None,
        scheduled_sync_enabled=True,
        scheduled_sync_fallback_hours=fallback_hours,
    )


_FALLBACK_HOURS_24 = 24
_EXPECTED_TWO_CALLS = 2


class TestCheckAndSyncOverdueItems:
    """Tests for _check_and_sync_overdue_items()."""

    def test_overdue_item_triggers_sync(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Item last synced more than fallback_hours ago triggers sync."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        old_ts = (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat()
        with sqlite3.connect(db_path) as conn:
            upsert_sync_state(
                conn, item_id="bank-alice", cursor=None, last_synced_at=old_ts
            )

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_ALICE", "tok-alice")
        # S106: access_token_env holds an env-var name, not a token literal.
        item_cfg = ItemConfig(
            id="bank-alice",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_ALICE",  # noqa: S106
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.server.load_items_config",
                return_value=[item_cfg],
            ),
            patch("claw_plaid_ledger.server._background_sync", mock_bg),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        mock_bg.assert_called_once()
        call_kwargs = mock_bg.call_args.kwargs
        assert call_kwargs["item_id"] == "bank-alice"

    def test_recent_item_skipped(self, tmp_path: pathlib.Path) -> None:
        """An item synced within the fallback window is not re-synced."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        recent_ts = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
        with sqlite3.connect(db_path) as conn:
            upsert_sync_state(
                conn,
                item_id="bank-alice",
                cursor=None,
                last_synced_at=recent_ts,
            )

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        # S106: access_token_env holds an env-var name, not a token literal.
        item_cfg = ItemConfig(
            id="bank-alice",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_ALICE",  # noqa: S106
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.server.load_items_config",
                return_value=[item_cfg],
            ),
            patch("claw_plaid_ledger.server._background_sync", mock_bg),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        mock_bg.assert_not_called()

    def test_item_with_no_sync_state_treated_as_overdue(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """An item with no entry in sync_state is treated as overdue."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_BOB", "tok-bob")
        # S106: access_token_env holds an env-var name, not a token literal.
        item_cfg = ItemConfig(
            id="bank-bob",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_BOB",  # noqa: S106
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.server.load_items_config",
                return_value=[item_cfg],
            ),
            patch("claw_plaid_ledger.server._background_sync", mock_bg),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        mock_bg.assert_called_once()

    def test_one_item_failure_does_not_prevent_others(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Exception for one item is caught; others are still checked."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_ALICE", "tok-alice")
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_BOB", "tok-bob")
        # S106: access_token_env holds an env-var name, not a token literal.
        item_alice = ItemConfig(
            id="bank-alice",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_ALICE",  # noqa: S106
        )
        item_bob = ItemConfig(
            id="bank-bob",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_BOB",  # noqa: S106
        )

        call_count = 0
        _err_msg = "alice sync failed"

        async def _flaky_bg(**kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if kwargs.get("item_id") == "bank-alice":
                raise RuntimeError(_err_msg)

        with (
            patch(
                "claw_plaid_ledger.server.load_items_config",
                return_value=[item_alice, item_bob],
            ),
            patch(
                "claw_plaid_ledger.server._background_sync",
                side_effect=_flaky_bg,
            ),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        # bank-bob must have been reached even though bank-alice raised
        assert call_count == _EXPECTED_TWO_CALLS

    def test_no_items_toml_uses_single_item_fallback(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When items.toml is absent, the single-item fallback is checked."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.server.load_items_config", return_value=[]
            ),
            patch("claw_plaid_ledger.server._background_sync", mock_bg),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        # _background_sync called with no arguments (single-item path)
        mock_bg.assert_called_once_with()


# ---------------------------------------------------------------------------
# Tests for _scheduled_sync_loop
# ---------------------------------------------------------------------------


_EXPECTED_ONE_CALL = 1


class TestScheduledSyncLoop:
    """Tests for _scheduled_sync_loop()."""

    def test_loop_calls_check_after_sleep(
        self, tmp_path: pathlib.Path
    ) -> None:
        """_scheduled_sync_loop calls _check_and_sync_overdue_items."""
        cfg = _make_scheduled_sync_config(tmp_path)

        check_calls: list[object] = []

        async def _fake_check(config: Config) -> None:
            check_calls.append(config)
            # Stop the loop after the first check by raising CancelledError.
            raise asyncio.CancelledError

        async def _run() -> None:
            with (
                patch(
                    "claw_plaid_ledger.server.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "claw_plaid_ledger.server._check_and_sync_overdue_items",
                    side_effect=_fake_check,
                ),
                pytest.raises(asyncio.CancelledError),
            ):
                await _scheduled_sync_loop(cfg)

        asyncio.run(_run())
        assert len(check_calls) == _EXPECTED_ONE_CALL
        assert check_calls[0] is cfg


# ---------------------------------------------------------------------------
# Tests for GET /spend
# ---------------------------------------------------------------------------


_SPEND_JAN_CANONICAL_COUNT = 3
_SPEND_JAN_CANONICAL_TOTAL = 350.0
_SPEND_JAN_GROCERIES_COUNT = 2
_SPEND_JAN_GROCERIES_TOTAL = 150.0
_SPEND_JAN_ALICE_COUNT = 2
_SPEND_JAN_ALICE_TOTAL = 150.0
_SPEND_JAN_PENDING_COUNT = 4
_SPEND_JAN_PENDING_TOTAL = 425.0
_SPEND_JAN_RAW_COUNT = 4
_SPEND_JAN_RAW_TOTAL = 1349.0


def _seed_spend_data(db_path: pathlib.Path) -> None:
    """
    Seed richer fixture data for GET /spend tests.

    Accounts:
      acct_alice  owner=alice   canonical (not suppressed)
      acct_bob    owner=bob     canonical
      acct_dup    owner=alice   suppressed (canonical_account_id=acct_alice)

    Transactions (non-pending unless noted):
      tx_a1  acct_alice  100.0  posted 2025-01-10
      tx_a2  acct_alice   50.0  posted 2025-01-20
      tx_b1  acct_bob    200.0  posted 2025-01-15
      tx_ap  acct_alice   75.0  PENDING authorized 2025-01-05
      tx_af  acct_alice   30.0  posted 2025-02-10
      tx_dup acct_dup    999.0  posted 2025-01-10

    Annotations:
      tx_a1  tags=["groceries","food"]
      tx_a2  tags=["groceries"]
      tx_b1  tags=["transport"]
      (tx_ap, tx_af, tx_dup have no annotations)
    """
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            (
                "INSERT INTO accounts (plaid_account_id, name, owner, "
                "canonical_account_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "acct_alice",
                    "Alice Bank",
                    "alice",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "acct_bob",
                    "Bob Bank",
                    "bob",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "acct_dup",
                    "Alice Dup",
                    "alice",
                    "acct_alice",
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.executemany(
            (
                "INSERT INTO transactions (plaid_transaction_id, "
                "plaid_account_id, amount, iso_currency_code, name, "
                "merchant_name, pending, authorized_date, posted_date, "
                "raw_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_a1",
                    "acct_alice",
                    100.0,
                    "USD",
                    "Grocery Store",
                    None,
                    0,
                    None,
                    "2025-01-10",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_a2",
                    "acct_alice",
                    50.0,
                    "USD",
                    "Farmer Market",
                    None,
                    0,
                    None,
                    "2025-01-20",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_b1",
                    "acct_bob",
                    200.0,
                    "USD",
                    "Transit",
                    None,
                    0,
                    None,
                    "2025-01-15",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_ap",
                    "acct_alice",
                    75.0,
                    "USD",
                    "Pending Charge",
                    None,
                    1,
                    "2025-01-05",
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_af",
                    "acct_alice",
                    30.0,
                    "USD",
                    "Coffee",
                    None,
                    0,
                    None,
                    "2025-02-10",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_dup",
                    "acct_dup",
                    999.0,
                    "USD",
                    "Dup Charge",
                    None,
                    0,
                    None,
                    "2025-01-10",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.executemany(
            (
                "INSERT INTO annotations (plaid_transaction_id, category, "
                "note, tags, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_a1",
                    "food",
                    None,
                    '["groceries","food"]',
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_a2",
                    "food",
                    None,
                    '["groceries"]',
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_b1",
                    "transport",
                    None,
                    '["transport"]',
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
            ],
        )


class TestGetSpendEndpoint:
    """Tests for GET /spend endpoint behavior."""

    def test_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Missing Authorization header returns 401."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/spend",
            params={"start_date": "2025-01-01", "end_date": "2025-01-31"},
        )

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_missing_start_date_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Omitting start_date returns HTTP 422."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/spend",
            params={"end_date": "2025-01-31"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_missing_end_date_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Omitting end_date returns HTTP 422."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/spend",
            params={"start_date": "2025-01-01"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_invalid_date_format_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Non-ISO date strings return HTTP 422."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/spend",
            params={"start_date": "not-a-date", "end_date": "2025-01-31"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_basic_spend_in_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Basic window returns correct total_spend and transaction_count."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # Canonical non-pending Jan 2025: tx_a1(100)+tx_a2(50)+tx_b1(200)
        response = client.get(
            "/spend",
            params={"start_date": "2025-01-01", "end_date": "2025-01-31"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2025-01-01"
        assert body["end_date"] == "2025-01-31"
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_CANONICAL_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_CANONICAL_COUNT
        assert body["includes_pending"] is False
        assert body["filters"]["owner"] is None
        assert body["filters"]["tags"] == []

    def test_empty_window_returns_zeros(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Date window with no matches returns zeros, not an error."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/spend",
            params={"start_date": "2020-01-01", "end_date": "2020-01-31"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == 0.0
        assert body["transaction_count"] == 0

    def test_single_tag_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tags=groceries restricts to transactions with that tag."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tx_a1(100) + tx_a2(50) both have "groceries" tag
        response = client.get(
            "/spend",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "tags": "groceries",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_GROCERIES_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_GROCERIES_COUNT
        assert body["filters"]["tags"] == ["groceries"]

    def test_two_tag_and_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tags=groceries&tags=food restricts to transactions with both."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # Only tx_a1(100) has both "groceries" AND "food"
        response = client.get(
            "/spend",
            params=[
                ("start_date", "2025-01-01"),
                ("end_date", "2025-01-31"),
                ("tags", "groceries"),
                ("tags", "food"),
            ],
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        _two_tag_count = 1
        _two_tag_total = 100.0
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == pytest.approx(_two_tag_total)
        assert body["transaction_count"] == _two_tag_count

    def test_tag_no_match_returns_zeros(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """A tag that matches nothing returns zeros."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/spend",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "tags": "nonexistent-tag",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == 0.0
        assert body["transaction_count"] == 0

    def test_include_pending_false_excludes_pending(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Default include_pending=false excludes pending transactions."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tx_ap (75.0 pending) must NOT appear; tx_a1+tx_a2+tx_b1=350
        response = client.get(
            "/spend",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "include_pending": "false",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_CANONICAL_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_CANONICAL_COUNT
        assert body["includes_pending"] is False

    def test_include_pending_true_includes_pending(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """include_pending=true adds pending transactions to the total."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tx_a1(100) + tx_a2(50) + tx_b1(200) + tx_ap(75) = 425
        response = client.get(
            "/spend",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "include_pending": "true",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_PENDING_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_PENDING_COUNT
        assert body["includes_pending"] is True

    def test_owner_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?owner=alice restricts spend to Alice's accounts."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # acct_alice owner=alice: tx_a1(100) + tx_a2(50) = 150
        # acct_dup is suppressed (canonical_account_id set) so excluded
        response = client.get(
            "/spend",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "owner": "alice",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_ALICE_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_ALICE_COUNT
        assert body["filters"]["owner"] == "alice"

    def test_view_raw_includes_suppressed_accounts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """view=raw includes transactions from suppressed accounts."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # Raw view: tx_a1(100) + tx_a2(50) + tx_b1(200) + tx_dup(999) = 1349
        response = client.get(
            "/spend",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "view": "raw",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_RAW_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_RAW_COUNT

    def test_view_canonical_excludes_suppressed_accounts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """view=canonical excludes suppressed-account transactions."""
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # Canonical: tx_dup on acct_dup (suppressed) is excluded → 350
        response = client.get(
            "/spend",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "view": "canonical",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert (
            response.json()["transaction_count"] == _SPEND_JAN_CANONICAL_COUNT
        )
        assert response.json()["total_spend"] == pytest.approx(
            _SPEND_JAN_CANONICAL_TOTAL
        )


# ---------------------------------------------------------------------------
# Tests for GET /transactions — tags and search_notes filters (Task 2)
# ---------------------------------------------------------------------------


def _seed_tag_notes_data(db_path: pathlib.Path) -> None:
    """
    Seed fixture data for tags and search_notes filter tests.

    Accounts:
      acct_1  (canonical — not suppressed)

    Transactions:
      tx_t1  name="Tea House"   note="morning coffee habit"  tags=["coffee"]
      tx_t2  name="Coffee Shop" note="nothing special"       tags=["groceries"]
      tx_t3  name="Bookstore"   no annotation

    Note: tx_t1 also has tag "recurring" (stored as ["coffee","recurring"]).
    """
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "acct_1",
                "Checking",
                "2025-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        connection.executemany(
            (
                "INSERT INTO transactions (plaid_transaction_id, "
                "plaid_account_id, amount, iso_currency_code, name, "
                "merchant_name, pending, authorized_date, posted_date, "
                "raw_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_t1",
                    "acct_1",
                    5.0,
                    "USD",
                    "Tea House",
                    None,
                    0,
                    None,
                    "2025-01-10",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t2",
                    "acct_1",
                    12.0,
                    "USD",
                    "Coffee Shop",
                    None,
                    0,
                    None,
                    "2025-01-11",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t3",
                    "acct_1",
                    20.0,
                    "USD",
                    "Bookstore",
                    None,
                    0,
                    None,
                    "2025-01-12",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
            ],
        )
        # Annotate tx_t1: tags=["coffee","recurring"], note matching "coffee"
        connection.execute(
            (
                "INSERT INTO annotations (plaid_transaction_id, category, "
                "note, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (
                "tx_t1",
                None,
                "morning coffee habit",
                '["coffee", "recurring"]',
                "2025-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        # Annotate tx_t2: tags=["groceries"], note="nothing special"
        connection.execute(
            (
                "INSERT INTO annotations (plaid_transaction_id, category, "
                "note, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (
                "tx_t2",
                None,
                "nothing special",
                '["groceries"]',
                "2025-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        # tx_t3 has no annotation


class TestListTransactionsTagsAndSearchNotes:
    """Tests for ?tags and ?search_notes filters on GET /transactions."""

    def test_single_tag_filter_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tags=coffee returns only transactions annotated with 'coffee'."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"tags": "coffee"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total"] == 1
        assert body["transactions"][0]["id"] == "tx_t1"

    def test_single_tag_filter_no_match(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tags=nonexistent returns an empty list, not an error."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"tags": "nonexistent"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json()["total"] == 0
        assert response.json()["transactions"] == []

    def test_and_two_tags_matches_intersection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tags=coffee&tags=recurring returns only transactions with both."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tx_t1 has ["coffee","recurring"]; tx_t2 has ["groceries"] only
        response = client.get(
            "/transactions",
            params=[("tags", "coffee"), ("tags", "recurring")],
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total"] == 1
        assert body["transactions"][0]["id"] == "tx_t1"

    def test_and_two_tags_no_common_match_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tags=coffee&tags=groceries returns empty — no tx has both."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params=[("tags", "coffee"), ("tags", "groceries")],
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json()["total"] == 0

    def test_unannotated_transaction_never_matches_tag_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """tx_t3 (no annotation) never appears when a tag filter is active."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"tags": "coffee"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        ids = [t["id"] for t in response.json()["transactions"]]
        assert "tx_t3" not in ids

    def test_keyword_without_search_notes_does_not_match_note(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Keyword with search_notes=false does not match the note field."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tx_t1 name="Tea House" (note has "coffee" but name does NOT match)
        # tx_t2 name="Coffee Shop" → name matches
        response = client.get(
            "/transactions",
            params={"keyword": "coffee", "search_notes": "false"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        ids = [t["id"] for t in body["transactions"]]
        assert "tx_t2" in ids
        assert "tx_t1" not in ids

    def test_search_notes_true_also_matches_note_field(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Search_notes=true extends keyword search to the annotation note."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tx_t1 note="morning coffee habit" → note matches
        # tx_t2 name="Coffee Shop" → name matches
        _two_matches = 2
        response = client.get(
            "/transactions",
            params={"keyword": "coffee", "search_notes": "true"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total"] == _two_matches
        ids = {t["id"] for t in body["transactions"]}
        assert ids == {"tx_t1", "tx_t2"}

    def test_combined_tags_keyword_search_notes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Combining tags + keyword + search_notes all apply together (AND)."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tags=coffee → only tx_t1
        # keyword=coffee + search_notes=true → tx_t1 (note) and tx_t2 (name)
        # combined (AND): must satisfy both → only tx_t1
        response = client.get(
            "/transactions",
            params={
                "tags": "coffee",
                "keyword": "coffee",
                "search_notes": "true",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total"] == 1
        assert body["transactions"][0]["id"] == "tx_t1"

    def test_no_new_params_regression(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """No new params returns all canonical transactions unchanged."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        _three_txns = 3
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        # All three transactions are in the same canonical account
        assert body["total"] == _three_txns
        ids = {t["id"] for t in body["transactions"]}
        assert ids == {"tx_t1", "tx_t2", "tx_t3"}

    def test_total_reflects_filtered_count_for_pagination(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Total reflects tag-filtered count, enabling correct pagination."""
        db_path = tmp_path / "db.sqlite"
        _seed_tag_notes_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # tags=coffee → 1 match; limit=10 returns it, total must be 1
        response = client.get(
            "/transactions",
            params={"tags": "coffee", "limit": "10", "offset": "0"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["total"] == 1
        assert len(body["transactions"]) == 1


# ---------------------------------------------------------------------------
# Tests for CorrelationIdMiddleware
# ---------------------------------------------------------------------------


_REQUEST_ID_LENGTH = 12  # "req-" (4) + 8 hex chars


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
        with caplog.at_level(logging.INFO, logger="claw_plaid_ledger.server"):
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
            caplog.at_level(logging.INFO, logger="claw_plaid_ledger.server"),
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.server.PlaidClientAdapter") as mock_cls,
            patch(
                "claw_plaid_ledger.server.run_sync",
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
            caplog.at_level(logging.INFO, logger="claw_plaid_ledger.server"),
            patch(
                "claw_plaid_ledger.server.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.server.PlaidClientAdapter") as mock_cls,
            patch(
                "claw_plaid_ledger.server.run_sync",
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

        body = b'{"webhook_type": "SYNC_UPDATES_AVAILABLE"}'
        sig = _make_plaid_sig(body)

        captured_sync_run_id: list[str] = []

        async def _capture_sync(*, sync_run_id: str | None = None) -> None:
            if sync_run_id is not None:
                captured_sync_run_id.append(sync_run_id)

        monkeypatch.setattr(
            "claw_plaid_ledger.server._background_sync", _capture_sync
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


# ---------------------------------------------------------------------------
# Unit tests for IP resolution helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Integration tests for WebhookIPAllowlistMiddleware
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests for GET /categories endpoint
# ---------------------------------------------------------------------------


def _insert_one_annotation_row(
    db_path: pathlib.Path,
    *,
    category: str | None,
    tags: str | None,
    note: str | None = None,
) -> None:
    """Insert a single account, transaction, and annotation row for tests."""
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO accounts "
            "(plaid_account_id, name, created_at, updated_at)"
            " VALUES ('acct_1', 'A', '2024-01-01', '2024-01-01')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO transactions "
            "(plaid_transaction_id, plaid_account_id,"
            " amount, iso_currency_code, name, merchant_name, pending,"
            " authorized_date, posted_date, raw_json, created_at, updated_at)"
            " VALUES ('tx_1', 'acct_1', 5.0, 'USD', 'X', 'X', 0, NULL,"
            " '2024-01-01', NULL, '2024-01-01', '2024-01-01')"
        )
        connection.execute(
            "INSERT INTO annotations "
            "(plaid_transaction_id, category, note, tags,"
            " created_at, updated_at)"
            " VALUES ('tx_1', ?, ?, ?, '2024-01-01', '2024-01-01')",
            (category, note, tags),
        )


def _seed_annotations(db_path: pathlib.Path) -> None:
    """Seed a DB with transactions and annotations for vocabulary tests."""
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            (
                "INSERT INTO accounts ("
                "plaid_account_id, name, created_at, updated_at"
                ") VALUES (?, ?, ?, ?)"
            ),
            [
                (
                    "acct_1",
                    "Account 1",
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.executemany(
            (
                "INSERT INTO transactions ("
                "plaid_transaction_id, plaid_account_id, amount, "
                "iso_currency_code, name, merchant_name, pending, "
                "authorized_date, posted_date, raw_json, created_at, "
                "updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_1",
                    "acct_1",
                    10.0,
                    "USD",
                    "Coffee",
                    "Coffee Shop",
                    0,
                    None,
                    "2024-01-15",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "tx_2",
                    "acct_1",
                    20.0,
                    "USD",
                    "Groceries",
                    "Supermarket",
                    0,
                    None,
                    "2024-01-16",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "tx_3",
                    "acct_1",
                    30.0,
                    "USD",
                    "Software",
                    "SaaS Co",
                    0,
                    None,
                    "2024-01-17",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "tx_4",
                    "acct_1",
                    40.0,
                    "USD",
                    "Bus Ticket",
                    "Transit",
                    0,
                    None,
                    "2024-01-18",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.executemany(
            (
                "INSERT INTO annotations ("
                "plaid_transaction_id, category, note, tags, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_1",
                    "food",
                    "morning coffee",
                    json.dumps(["discretionary", "recurring"]),
                    "2024-01-15T00:00:00+00:00",
                    "2024-01-15T00:00:00+00:00",
                ),
                (
                    "tx_2",
                    "Food",  # duplicate with different case
                    None,
                    json.dumps(["needs-review", "recurring"]),
                    "2024-01-16T00:00:00+00:00",
                    "2024-01-16T00:00:00+00:00",
                ),
                (
                    "tx_3",
                    "software",
                    None,
                    json.dumps(["subscription"]),
                    "2024-01-17T00:00:00+00:00",
                    "2024-01-17T00:00:00+00:00",
                ),
                (
                    "tx_4",
                    "transport",
                    None,
                    None,  # no tags
                    "2024-01-18T00:00:00+00:00",
                    "2024-01-18T00:00:00+00:00",
                ),
            ],
        )


class TestCategoriesEndpoint:
    """Tests for GET /categories endpoint."""

    def test_requires_auth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unauthenticated request returns 401."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        initialize_database(tmp_path / "db.sqlite")

        response = client.get("/categories")

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_empty_annotations_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """No annotations → empty categories array."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/categories", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {"categories": []}

    def test_returns_sorted_distinct_categories(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Returns alphabetically sorted distinct categories."""
        db_path = tmp_path / "db.sqlite"
        _seed_annotations(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/categories", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        data = response.json()
        assert "categories" in data
        # food, Food, software, transport → 4 distinct values
        expected_count = 4
        assert len(data["categories"]) == expected_count
        # Sorted alphabetically (case-insensitive collation)
        lower = [c.lower() for c in data["categories"]]
        assert lower == sorted(lower)

    def test_excludes_null_categories(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Annotations with null category are excluded."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        _insert_one_annotation_row(
            db_path, category=None, tags=None, note="no category"
        )
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/categories", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {"categories": []}


class TestTagsEndpoint:
    """Tests for GET /tags endpoint."""

    def test_requires_auth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unauthenticated request returns 401."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        initialize_database(tmp_path / "db.sqlite")

        response = client.get("/tags")

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_empty_annotations_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """No annotations → empty tags array."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/tags", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {"tags": []}

    def test_returns_sorted_distinct_tags(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Returns alphabetically sorted distinct unnested tag values."""
        db_path = tmp_path / "db.sqlite"
        _seed_annotations(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/tags", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        data = response.json()
        assert "tags" in data
        # discretionary, needs-review, recurring, subscription
        expected_count = 4
        assert len(data["tags"]) == expected_count
        assert data["tags"] == sorted(data["tags"], key=str.lower)

    def test_excludes_null_tags(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Annotations with null tags are excluded from the result."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        _insert_one_annotation_row(
            db_path, category="food", tags=None, note="no tags"
        )
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/tags", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {"tags": []}

    def test_deduplicates_tags_across_rows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Tags in multiple annotation rows are returned only once."""
        db_path = tmp_path / "db.sqlite"
        _seed_annotations(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/tags", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        tags = response.json()["tags"]
        # "recurring" appears in tx_1 and tx_2; should appear only once
        assert tags.count("recurring") == 1
