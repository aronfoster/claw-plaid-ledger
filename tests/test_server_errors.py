"""Tests for the GET /errors endpoint."""

from __future__ import annotations

import http
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from claw_plaid_ledger.db import (
    LedgerErrorRow,
    initialize_database,
    insert_ledger_error,
)
from claw_plaid_ledger.server import app

if TYPE_CHECKING:
    import pathlib

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105


class TestGetErrorsEndpoint:
    """Tests for the GET /errors endpoint."""

    def _setup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> pathlib.Path:
        """Initialize a fresh DB and configure env vars for the endpoint."""
        db_path = tmp_path / "test.db"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        return db_path

    def _insert_row(
        self,
        db_path: pathlib.Path,
        *,
        severity: str = "WARNING",
        message: str = "test message",
        created_at: datetime | None = None,
    ) -> None:
        """Insert one ledger_error row directly into the DB."""
        if created_at is None:
            created_at = datetime.now(UTC)
        row = LedgerErrorRow(
            severity=severity,
            logger_name="test.logger",
            message=message,
            correlation_id=None,
            created_at=created_at,
        )
        with sqlite3.connect(db_path) as conn:
            insert_ledger_error(conn, row)

    def test_returns_200_with_auth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Authenticated request returns HTTP 200."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/errors",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK

    def test_requires_auth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unauthenticated request returns HTTP 401."""
        self._setup(monkeypatch, tmp_path)
        response = client.get("/errors")
        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_empty_table_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """No rows in DB; response has errors=[], total=0."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/errors",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        data = response.json()
        assert data["errors"] == []
        assert data["total"] == 0

    def test_returns_errors_within_hours_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Default hours=24 returns only rows within the last 24h."""
        db_path = self._setup(monkeypatch, tmp_path)
        recent = datetime.now(UTC)
        old = recent - timedelta(hours=48)
        self._insert_row(db_path, message="recent", created_at=recent)
        self._insert_row(db_path, message="old", created_at=old)

        response = client.get(
            "/errors",
            params={"hours": 24},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        data = response.json()
        assert data["total"] == 1
        assert data["errors"][0]["message"] == "recent"

    def test_min_severity_error_excludes_warnings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?min_severity=ERROR excludes WARNING rows."""
        db_path = self._setup(monkeypatch, tmp_path)
        self._insert_row(db_path, severity="WARNING", message="warn row")
        self._insert_row(db_path, severity="ERROR", message="error row")

        response = client.get(
            "/errors",
            params={"min_severity": "ERROR"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        data = response.json()
        assert data["total"] == 1
        assert data["errors"][0]["severity"] == "ERROR"

    def test_pagination_limit_and_offset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?limit=2&offset=2 returns 2 rows with total=5."""
        db_path = self._setup(monkeypatch, tmp_path)
        total_rows = 5
        page_limit = 2
        page_offset = 2
        for i in range(total_rows):
            self._insert_row(db_path, message=f"msg-{i}")

        response = client.get(
            "/errors",
            params={"limit": page_limit, "offset": page_offset},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        data = response.json()
        assert data["total"] == total_rows
        assert len(data["errors"]) == page_limit
        assert data["limit"] == page_limit
        assert data["offset"] == page_offset

    def test_since_field_present_in_response(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Response contains 'since' as a valid ISO datetime string."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/errors",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        data = response.json()
        assert "since" in data
        # Verify it parses as a valid ISO datetime.
        since_dt = datetime.fromisoformat(data["since"])
        assert since_dt.tzinfo is not None

    def test_hours_validation_rejects_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?hours=0 returns HTTP 422 (ge=1 constraint)."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/errors",
            params={"hours": 0},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
