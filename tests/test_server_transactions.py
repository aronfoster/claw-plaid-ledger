"""Tests for GET /transactions and GET /transactions/{id} endpoints."""

from __future__ import annotations

import http
import sqlite3
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from claw_plaid_ledger.db import initialize_database
from claw_plaid_ledger.server import app
from tests.helpers import _RANGE_TODAY, _patch_today, _seed_transactions

if TYPE_CHECKING:
    import pathlib

    import pytest

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105

# tx_1 amount from the _seed_transactions helper — used for PLR2004 avoidance.
_TX_1_AMOUNT = 12.34


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
                    "annotation": None,
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


# ---------------------------------------------------------------------------
# Tests for GET /transactions — range shorthand parameter (BUG-012)
# ---------------------------------------------------------------------------


def _seed_range_transactions(db_path: pathlib.Path) -> None:
    """Seed transactions across date ranges for range-param tests."""
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            (
                "INSERT INTO accounts "
                "(plaid_account_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)"
            ),
            (
                "acct_r",
                "Range Account",
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
            ),
        )
        connection.executemany(
            (
                "INSERT INTO transactions ("
                "plaid_transaction_id, plaid_account_id, amount, "
                "iso_currency_code, name, merchant_name, pending, "
                "authorized_date, posted_date, raw_json, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                # Feb 2026 — inside last_month when today=2026-03-21
                (
                    "tx_feb1",
                    "acct_r",
                    10.0,
                    "USD",
                    "Feb Mid",
                    None,
                    0,
                    None,
                    "2026-02-15",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "tx_feb2",
                    "acct_r",
                    20.0,
                    "USD",
                    "Feb Start",
                    None,
                    0,
                    None,
                    "2026-02-01",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                # Mar 2026, within last_7_days (2026-03-14..2026-03-21)
                (
                    "tx_mar1",
                    "acct_r",
                    30.0,
                    "USD",
                    "Mar Recent",
                    None,
                    0,
                    None,
                    "2026-03-18",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                # Mar 2026, outside last_7_days but inside this_month
                (
                    "tx_mar2",
                    "acct_r",
                    40.0,
                    "USD",
                    "Mar Early",
                    None,
                    0,
                    None,
                    "2026-03-01",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                # Jan 2026 — outside all ranges
                (
                    "tx_jan1",
                    "acct_r",
                    50.0,
                    "USD",
                    "Jan",
                    None,
                    0,
                    None,
                    "2026-01-15",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )


class TestListTransactionsRangeParam:
    """Tests for the ``range`` shorthand on GET /transactions (BUG-012)."""

    def _setup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _seed_range_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        _patch_today(monkeypatch, _RANGE_TODAY)

    def test_last_month_returns_only_february_transactions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=last_month returns Feb 2026 transactions only."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/transactions",
            params={"range": "last_month"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        ids = {t["id"] for t in body["transactions"]}
        assert ids == {"tx_feb1", "tx_feb2"}
        expected_total = 2
        assert body["total"] == expected_total

    def test_this_month_returns_march_transactions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=this_month returns Mar 2026 transactions only."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/transactions",
            params={"range": "this_month"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        ids = {t["id"] for t in response.json()["transactions"]}
        assert ids == {"tx_mar1", "tx_mar2"}

    def test_last_7_days_returns_recent_transactions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=last_7_days returns transactions within 7 days of today."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/transactions",
            params={"range": "last_7_days"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        ids = {t["id"] for t in response.json()["transactions"]}
        assert ids == {"tx_mar1"}

    def test_explicit_start_date_overrides_range(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Explicit start_date overrides the range-derived start date."""
        self._setup(monkeypatch, tmp_path)
        # last_month derives 2026-02-01..2026-02-28; push start to 2026-02-10
        response = client.get(
            "/transactions",
            params={"range": "last_month", "start_date": "2026-02-10"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        ids = {t["id"] for t in response.json()["transactions"]}
        assert "tx_feb1" in ids  # 2026-02-15 — after override start
        assert "tx_feb2" not in ids  # 2026-02-01 — before override start

    def test_explicit_end_date_overrides_range(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Explicit end_date overrides the range-derived end date."""
        self._setup(monkeypatch, tmp_path)
        # last_month derives ..2026-02-28; pull end back to 2026-02-10
        response = client.get(
            "/transactions",
            params={"range": "last_month", "end_date": "2026-02-10"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        ids = {t["id"] for t in response.json()["transactions"]}
        assert "tx_feb2" in ids  # 2026-02-01 — before override end
        assert "tx_feb1" not in ids  # 2026-02-15 — after override end

    def test_no_range_returns_full_history(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Without range, all transactions are returned."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/transactions",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        expected_total = 5
        assert response.json()["total"] == expected_total

    def test_invalid_range_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unrecognised range value is rejected with HTTP 422."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/transactions",
            params={"range": "yesterday"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# Tests for GET /transactions — annotations in list results (BUG-013)
# ---------------------------------------------------------------------------


def _seed_annotation_list_data(db_path: pathlib.Path) -> None:
    """Seed two transactions: one annotated, one bare."""
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            (
                "INSERT INTO accounts "
                "(plaid_account_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)"
            ),
            (
                "acct_a",
                "Account A",
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
            ),
        )
        connection.executemany(
            (
                "INSERT INTO transactions ("
                "plaid_transaction_id, plaid_account_id, amount, "
                "iso_currency_code, name, merchant_name, pending, "
                "authorized_date, posted_date, raw_json, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_ann",
                    "acct_a",
                    15.0,
                    "USD",
                    "Coffee Shop",
                    "Bean Barn",
                    0,
                    None,
                    "2024-06-01",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
                (
                    "tx_bare",
                    "acct_a",
                    25.0,
                    "USD",
                    "Gas Station",
                    None,
                    0,
                    None,
                    "2024-06-02",
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.execute(
            (
                "INSERT INTO annotations "
                "(plaid_transaction_id, category, note, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (
                "tx_ann",
                "coffee",
                "morning latte",
                '["coffee", "recurring"]',
                "2024-06-01T10:00:00+00:00",
                "2024-06-01T10:00:00+00:00",
            ),
        )
        # Seed allocations (mirrors production seeding; tx_ann includes
        # semantic fields to match the annotation already written above).
        connection.executemany(
            (
                "INSERT INTO allocations "
                "(plaid_transaction_id, amount, category, note, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_ann",
                    15.0,
                    "coffee",
                    "morning latte",
                    '["coffee", "recurring"]',
                    "2024-06-01T10:00:00+00:00",
                    "2024-06-01T10:00:00+00:00",
                ),
                (
                    "tx_bare",
                    25.0,
                    None,
                    None,
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                ),
            ],
        )


class TestListTransactionsAnnotations:
    """Tests for annotation data in GET /transactions results (BUG-013)."""

    def test_unannotated_transaction_has_null_annotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Unannotated transactions return annotation: null in list results."""
        db_path = tmp_path / "db.sqlite"
        _seed_annotation_list_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"start_date": "2024-06-02", "end_date": "2024-06-02"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        tx = response.json()["transactions"][0]
        assert tx["id"] == "tx_bare"
        assert tx["annotation"] is None

    def test_annotated_transaction_includes_annotation_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Annotated transactions include the full annotation object."""
        db_path = tmp_path / "db.sqlite"
        _seed_annotation_list_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"start_date": "2024-06-01", "end_date": "2024-06-01"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        tx = response.json()["transactions"][0]
        assert tx["id"] == "tx_ann"
        assert tx["annotation"] == {
            "category": "coffee",
            "note": "morning latte",
            "tags": ["coffee", "recurring"],
            "updated_at": "2024-06-01T10:00:00+00:00",
        }

    def test_list_annotation_shape_matches_detail_endpoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """List has annotation key; detail has allocation key; same data."""
        db_path = tmp_path / "db.sqlite"
        _seed_annotation_list_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        list_resp = client.get(
            "/transactions",
            params={"start_date": "2024-06-01", "end_date": "2024-06-01"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        detail_resp = client.get(
            "/transactions/tx_ann",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert list_resp.status_code == http.HTTPStatus.OK
        assert detail_resp.status_code == http.HTTPStatus.OK
        # List still returns annotation key (updated to allocation in Task 3)
        list_annotation = list_resp.json()["transactions"][0]["annotation"]
        assert list_annotation["category"] == "coffee"
        assert list_annotation["note"] == "morning latte"
        assert list_annotation["tags"] == ["coffee", "recurring"]
        # Detail returns allocation key since Task 2
        detail_allocation = detail_resp.json()["allocation"]
        assert detail_allocation is not None
        assert detail_allocation["category"] == "coffee"
        assert detail_allocation["note"] == "morning latte"
        assert detail_allocation["tags"] == ["coffee", "recurring"]

    def test_mixed_page_has_annotation_and_null(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Mixed page sets annotation or null correctly per row."""
        db_path = tmp_path / "db.sqlite"
        _seed_annotation_list_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        txs = {t["id"]: t for t in response.json()["transactions"]}
        assert txs["tx_ann"]["annotation"] is not None
        assert txs["tx_bare"]["annotation"] is None


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

    def test_known_id_without_annotation_has_blank_allocation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Known transaction has blank allocation with null semantic fields."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["id"] == "tx_1"
        assert body["account_id"] == "acct_1"
        assert body["amount"] == _TX_1_AMOUNT
        assert body["name"] == "Starbucks"
        assert body["pending"] is False
        assert body["raw_json"] is None
        assert body["suppressed_by"] is None
        assert "annotation" not in body
        allocation = body["allocation"]
        assert allocation is not None
        assert allocation["amount"] == _TX_1_AMOUNT
        assert allocation["category"] is None
        assert allocation["note"] is None
        assert allocation["tags"] is None
        assert "id" in allocation
        assert "updated_at" in allocation

    def test_known_id_with_allocation_parses_tags_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Allocation tags are returned as parsed JSON list."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # Use PUT to write annotation + allocation (double-write).
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
        allocation = response.json()["allocation"]
        assert allocation is not None
        assert allocation["category"] == "food"
        assert allocation["note"] == "Morning coffee"
        assert allocation["tags"] == ["discretionary", "recurring"]

    def test_allocation_with_null_tags_returns_null_tags(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Allocation with NULL tags returns tags as null."""
        db_path = tmp_path / "db.sqlite"
        _seed_transactions(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        # Use PUT with no tags — allocation.tags should be null.
        client.put(
            "/annotations/tx_1",
            json={"category": "food", "note": "Morning coffee"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        response = client.get(
            "/transactions/tx_1",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
        assert response.json()["allocation"]["tags"] is None

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
# Tests for GET /transactions — strict query parameter enforcement (BUG-014)
# ---------------------------------------------------------------------------


class TestListTransactionsStrictParams:
    """Tests for BUG-014: unknown query parameters rejected with 422."""

    def test_misspelled_param_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """A misspelled parameter (offest) is rejected with HTTP 422."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"offest": "10"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
        body = response.json()
        assert "unrecognized" in body["detail"]
        assert "valid_parameters" in body["detail"]
        assert "offest" in body["detail"]["unrecognized"]

    def test_unrecognized_param_body_contains_required_keys(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """422 body contains 'unrecognized' and 'valid_parameters' keys."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"page": "2", "page_size": "50"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
        detail = response.json()["detail"]
        assert "unrecognized" in detail
        assert "valid_parameters" in detail
        assert sorted(detail["unrecognized"]) == ["page", "page_size"]

    def test_valid_params_are_accepted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """A request with all-valid parameters returns 200 (no regression)."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        response = client.get(
            "/transactions",
            params={"limit": "10", "offset": "0", "view": "canonical"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

        assert response.status_code == http.HTTPStatus.OK
