"""Tests for bearer-token authentication (require_bearer_token dependency)."""

from __future__ import annotations

import http
from unittest.mock import patch

import fastapi
import pytest
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from claw_plaid_ledger.middleware.auth import require_bearer_token

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105


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
            "claw_plaid_ledger.middleware.auth.load_api_secret",
            return_value=_TOKEN,
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
