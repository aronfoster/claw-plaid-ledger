"""Tests for the FastAPI server module."""

from __future__ import annotations

import hashlib
import hmac
import http
import logging
import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import fastapi
import pytest
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    import pathlib

from claw_plaid_ledger.db import get_annotation, initialize_database
from claw_plaid_ledger.server import app, require_bearer_token

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
