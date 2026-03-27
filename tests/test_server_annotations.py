"""Tests for PUT /annotations/{transaction_id} endpoint."""

from __future__ import annotations

import http
import sqlite3
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from claw_plaid_ledger.db import (
    get_allocations_for_transaction,
    get_annotation,
)
from claw_plaid_ledger.server import app
from tests.helpers import _seed_transactions

if TYPE_CHECKING:
    import pathlib

    import pytest

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105

# tx_1 amount from the _seed_transactions helper — used for PLR2004 avoidance.
_TX_1_AMOUNT = 12.34


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
        assert "allocations" in body
        assert "annotation" not in body
        # Allocation block must reflect values just written
        allocation = body["allocations"][0]
        assert allocation is not None
        assert allocation["category"] == "food"
        assert allocation["note"] == "Morning coffee"
        assert allocation["tags"] == ["discretionary"]
        assert allocation["updated_at"] is not None

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

    def test_second_put_response_reflects_updated_allocation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Second PUT (update) returns the newly updated allocation fields."""
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
        allocation = body["allocations"][0]
        assert allocation is not None
        assert allocation["category"] == "transport"
        assert allocation["note"] == "Updated note"
        assert allocation["tags"] == ["b", "c"]

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
        assert response.json()["allocations"][0]["tags"] == []

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

    def test_end_to_end_put_then_get_allocation_block_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT then GET; allocation block in detail response matches."""
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
        allocation = response.json()["allocations"][0]
        assert allocation is not None
        assert allocation["category"] == "food"
        assert allocation["note"] == "Morning coffee"
        assert allocation["tags"] == ["discretionary", "recurring"]

    def test_put_creates_allocation_with_correct_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT creates an allocation row with the correct fields."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        client.put(
            "/annotations/tx_1",
            json={
                "category": "food",
                "note": "Morning coffee",
                "tags": ["discretionary"],
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        with sqlite3.connect(db_path) as connection:
            allocs = get_allocations_for_transaction(connection, "tx_1")
        assert len(allocs) == 1
        alloc = allocs[0]
        assert alloc.amount == _TX_1_AMOUNT
        assert alloc.category == "food"
        assert alloc.note == "Morning coffee"
        assert alloc.tags == '["discretionary"]'

    def test_put_response_has_allocation_key_not_annotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT response contains allocation key, not annotation."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.put(
            "/annotations/tx_1",
            json={"category": "food"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert "allocations" in body
        assert "annotation" not in body
        assert body["allocations"][0]["category"] == "food"
        assert body["allocations"][0]["amount"] == _TX_1_AMOUNT

    _EXPECTED_SPLIT_COUNT = 2

    def test_put_returns_409_for_split_transaction(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """PUT /annotations/{id} returns 409 for a split transaction."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # _seed_transactions inserts one allocation for tx_1; add a second
        # to make it a split (>1 allocation triggers the 409).
        now = "2024-01-01T00:00:00+00:00"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO allocations "
                "(plaid_transaction_id, amount, "
                "category, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("tx_1", 6.17, "drinks", now, now),
            )

        response = client.put(
            "/annotations/tx_1",
            json={"category": "food"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.CONFLICT
        detail = response.json()["detail"]
        assert detail["allocation_count"] == self._EXPECTED_SPLIT_COUNT
        assert "allocation" in detail["message"].lower()
