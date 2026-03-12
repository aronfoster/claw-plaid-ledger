"""Tests for the FastAPI server module."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

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
        """Unrecognised webhook types are acknowledged with 200; no sync."""
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

    def test_put_creates_annotation_returns_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT creates a new annotation; response is {status: ok}."""
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
        assert response.json() == {"status": "ok"}

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
        mock_bg.assert_called_once_with()
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
        mock_bg.assert_called_once_with()

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
        mock_bg.assert_called_once_with()
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
        mock_bg.assert_called_once_with()
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
