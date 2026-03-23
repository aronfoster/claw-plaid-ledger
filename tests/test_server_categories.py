"""Tests for the GET /categories and GET /tags endpoints."""

from __future__ import annotations

import http
import json
import sqlite3
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from claw_plaid_ledger.db import initialize_database
from claw_plaid_ledger.server import app

if TYPE_CHECKING:
    import pathlib

    import pytest

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105


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
