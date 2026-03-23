"""Tests for GET /accounts and PUT /accounts/{account_id} endpoints."""

from __future__ import annotations

import http
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


def _seed_accounts(db_path: pathlib.Path) -> None:
    """Seed two accounts; add a label row for the first one only."""
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            (
                "INSERT INTO accounts ("
                "plaid_account_id, name, mask, type, subtype, "
                "institution_name, owner, item_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "acc_abc123",
                    "Plaid Checking",
                    "1234",
                    "depository",
                    "checking",
                    "bank-alice",
                    "alice",
                    "item-alice-001",
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "acc_def456",
                    "Plaid Savings",
                    "5678",
                    "depository",
                    "savings",
                    "bank-alice",
                    "alice",
                    "item-alice-001",
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.execute(
            (
                "INSERT INTO account_labels "
                "(plaid_account_id, label, description, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)"
            ),
            (
                "acc_abc123",
                "Alice Joint Checking",
                "Primary joint household account",
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
            ),
        )


class TestAccountsEndpoints:
    """Tests for GET /accounts and PUT /accounts/{account_id}."""

    def test_get_accounts_returns_200(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """GET /accounts returns HTTP 200 when accounts have been synced."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/accounts", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK

    def test_get_accounts_returns_full_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """GET /accounts returns all accounts in the response."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/accounts", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        _expected_account_count = 2
        data = response.json()
        assert "accounts" in data
        assert len(data["accounts"]) == _expected_account_count

    def test_get_accounts_empty_database(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """GET /accounts returns empty list when no accounts synced."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/accounts", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json() == {"accounts": []}

    def test_get_accounts_null_label_for_unlabelled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unlabelled accounts have null label and description fields."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/accounts", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        accounts = response.json()["accounts"]
        unlabelled = next(
            a for a in accounts if a["account_id"] == "acc_def456"
        )
        assert unlabelled["label"] is None
        assert unlabelled["description"] is None

    def test_get_accounts_includes_label_data(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Labelled account has label and description from account_labels."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/accounts", headers={"Authorization": f"Bearer {_TOKEN}"}
        )

        accounts = response.json()["accounts"]
        labelled = next(a for a in accounts if a["account_id"] == "acc_abc123")
        assert labelled["label"] == "Alice Joint Checking"
        assert labelled["description"] == "Primary joint household account"

    def test_get_accounts_requires_auth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """GET /accounts returns 401 without Authorization header."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get("/accounts")

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_put_account_label_returns_200(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT /accounts/{account_id} returns 200 after writing label data."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/accounts/acc_def456",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            json={
                "label": "Alice Savings",
                "description": "Household savings",
            },
        )

        assert response.status_code == http.HTTPStatus.OK

    def test_put_account_label_returns_full_record(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT response contains the values just written."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/accounts/acc_def456",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            json={
                "label": "Alice Savings",
                "description": "Household savings",
            },
        )

        data = response.json()
        assert data["account_id"] == "acc_def456"
        assert data["label"] == "Alice Savings"
        assert data["description"] == "Household savings"
        assert data["plaid_name"] == "Plaid Savings"

    def test_put_account_label_update(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """A second PUT returns the newly updated label fields."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        client.put(
            "/accounts/acc_abc123",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            json={"label": "First Label", "description": "First desc"},
        )
        response = client.put(
            "/accounts/acc_abc123",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            json={"label": "Updated Label", "description": "Updated desc"},
        )

        data = response.json()
        assert data["label"] == "Updated Label"
        assert data["description"] == "Updated desc"

    def test_put_account_label_404_unknown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT /accounts/{account_id} returns 404 for an unknown account ID."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/accounts/acc_unknown",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            json={"label": "Ghost", "description": None},
        )

        assert response.status_code == http.HTTPStatus.NOT_FOUND

    def test_put_account_label_requires_auth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT /accounts/{account_id} returns 401 without auth."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/accounts/acc_abc123",
            json={"label": "No auth"},
        )

        assert response.status_code == http.HTTPStatus.UNAUTHORIZED

    def test_put_account_label_null_clears_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT with null fields clears label and description."""
        db_path = tmp_path / "db.sqlite"
        _seed_accounts(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/accounts/acc_abc123",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            json={"label": None, "description": None},
        )

        data = response.json()
        assert data["label"] is None
        assert data["description"] is None
