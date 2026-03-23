"""Tests for the GET /spend endpoint."""

from __future__ import annotations

import http
import sqlite3
from datetime import date
from typing import TYPE_CHECKING, cast

import pytest
from fastapi.testclient import TestClient

from claw_plaid_ledger.db import initialize_database
from claw_plaid_ledger.server import app

if TYPE_CHECKING:
    import pathlib

client = TestClient(app)

# Short name so S105 ("hardcoded password") does not fire; this value carries
# no real security significance — it is only used as a test fixture.
_TOKEN = "test-bearer-value"  # noqa: S105

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

# Enriched-filter test constants derived from the _seed_spend_data fixture.
_SPEND_JAN_ACCT_ALICE_COUNT = 2
_SPEND_JAN_ACCT_ALICE_TOTAL = 150.0
_SPEND_JAN_CATEGORY_FOOD_COUNT = 2
_SPEND_JAN_CATEGORY_FOOD_TOTAL = 150.0
_SPEND_JAN_TAG_GROCERIES_COUNT = 2
_SPEND_JAN_TAG_GROCERIES_TOTAL = 150.0
_SPEND_JAN_TAG_FOOD_COUNT = 1
_SPEND_JAN_TAG_FOOD_TOTAL = 100.0

# Fixed "today" used in all date-range tests: 2026-03-21 (a Saturday)
_RANGE_TODAY = "2026-03-21"
# Derived windows relative to _RANGE_TODAY:
#   this_month:   2026-03-01 .. 2026-03-21
#   last_month:   2026-02-01 .. 2026-02-28  (Feb 2026 is not a leap year)
#   last_30_days: 2026-02-19 .. 2026-03-21  (30 days back from 2026-03-21)
#   last_7_days:  2026-03-14 .. 2026-03-21


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


def _patch_today(monkeypatch: pytest.MonkeyPatch, isodate: str) -> None:
    """Patch claw_plaid_ledger.server._today to return *isodate*."""
    fixed = date.fromisoformat(isodate)
    monkeypatch.setattr("claw_plaid_ledger.server._today", lambda: fixed)


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
# Tests for GET /spend — range shorthand parameter (Task 3 / BUG-010)
# ---------------------------------------------------------------------------


class TestGetSpendRangeParam:
    """Tests for the ``range`` shorthand query parameter on GET /spend."""

    def _setup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        _patch_today(monkeypatch, _RANGE_TODAY)

    def test_this_month_returns_correct_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=this_month resolves to first-of-month .. today."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "this_month"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2026-03-01"
        assert body["end_date"] == _RANGE_TODAY

    def test_last_month_returns_correct_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=last_month resolves to first..last day of prior month."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "last_month"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2026-02-01"
        assert body["end_date"] == "2026-02-28"

    def test_last_month_january_crosses_year_boundary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=last_month in January resolves to December of prior year."""

        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        _patch_today(monkeypatch, "2026-01-15")

        response = client.get(
            "/spend",
            params={"range": "last_month"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2025-12-01"
        assert body["end_date"] == "2025-12-31"

    def test_last_30_days_returns_correct_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=last_30_days returns today-30 .. today."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "last_30_days"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2026-02-19"
        assert body["end_date"] == _RANGE_TODAY

    def test_last_7_days_returns_correct_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?range=last_7_days returns today-7 .. today."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "last_7_days"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2026-03-14"
        assert body["end_date"] == _RANGE_TODAY

    def test_explicit_start_date_overrides_range(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Explicit start_date overrides the range-derived start date."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "this_month", "start_date": "2026-03-10"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2026-03-10"
        assert body["end_date"] == _RANGE_TODAY

    def test_explicit_end_date_overrides_range(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Explicit end_date overrides the range-derived end date."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "this_month", "end_date": "2026-03-15"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert body["start_date"] == "2026-03-01"
        assert body["end_date"] == "2026-03-15"

    def test_no_range_no_dates_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Omitting both range and start_date/end_date returns HTTP 422."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_unrecognised_range_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """An unrecognised range value returns HTTP 422."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "last_year"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_explicit_dates_without_range_still_work(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Existing calls with explicit start_date+end_date are unaffected."""

        self._setup(monkeypatch, tmp_path)
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

    def test_range_response_includes_resolved_dates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Response always includes the resolved start_date and end_date."""

        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend",
            params={"range": "last_month"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        body = response.json()
        assert "start_date" in body
        assert "end_date" in body
        assert body["start_date"] == "2026-02-01"
        assert body["end_date"] == "2026-02-28"


# ---------------------------------------------------------------------------
# Tests for GET /spend enriched filters (BUG-008 + BUG-009)
# ---------------------------------------------------------------------------


class TestGetSpendEnrichedFilters:
    """Tests for GET /spend enriched filters: account_id, category, tag."""

    def get_spend_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        params: dict[str, str],
    ) -> dict[str, object]:
        """Seed DB, call GET /spend with given params, return parsed JSON."""

        db_path = tmp_path / "db.sqlite"
        _seed_spend_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        merged: dict[str, str] = {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            **params,
        }
        response = client.get(
            "/spend",
            params=merged,
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        return cast("dict[str, object]", response.json())

    def test_account_id_restricts_spend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?account_id=acct_alice restricts spend to Alice's transactions."""
        body = self.get_spend_response(
            monkeypatch, tmp_path, {"account_id": "acct_alice"}
        )
        assert body["total_spend"] == pytest.approx(
            _SPEND_JAN_ACCT_ALICE_TOTAL
        )
        assert body["transaction_count"] == _SPEND_JAN_ACCT_ALICE_COUNT

    def test_unknown_account_id_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?account_id=<unknown> returns zero spend without error."""
        body = self.get_spend_response(
            monkeypatch, tmp_path, {"account_id": "acct_unknown"}
        )
        assert body["total_spend"] == 0.0
        assert body["transaction_count"] == 0

    def test_category_filter_restricts_spend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?category=food restricts spend to food-annotated transactions."""
        body = self.get_spend_response(
            monkeypatch, tmp_path, {"category": "food"}
        )
        assert body["total_spend"] == pytest.approx(
            _SPEND_JAN_CATEGORY_FOOD_TOTAL
        )
        assert body["transaction_count"] == _SPEND_JAN_CATEGORY_FOOD_COUNT

    def test_category_filter_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?category=Food (mixed case) matches category stored as 'food'."""
        body = self.get_spend_response(
            monkeypatch, tmp_path, {"category": "Food"}
        )
        assert body["total_spend"] == pytest.approx(
            _SPEND_JAN_CATEGORY_FOOD_TOTAL
        )
        assert body["transaction_count"] == _SPEND_JAN_CATEGORY_FOOD_COUNT

    def test_tag_filter_restricts_spend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tag=groceries restricts spend to transactions with that tag."""
        body = self.get_spend_response(
            monkeypatch, tmp_path, {"tag": "groceries"}
        )
        assert body["total_spend"] == pytest.approx(
            _SPEND_JAN_TAG_GROCERIES_TOTAL
        )
        assert body["transaction_count"] == _SPEND_JAN_TAG_GROCERIES_COUNT

    def test_tag_filter_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tag=Groceries (mixed case) matches tag stored as 'groceries'."""
        body = self.get_spend_response(
            monkeypatch, tmp_path, {"tag": "Groceries"}
        )
        assert body["total_spend"] == pytest.approx(
            _SPEND_JAN_TAG_GROCERIES_TOTAL
        )
        assert body["transaction_count"] == _SPEND_JAN_TAG_GROCERIES_COUNT

    def test_category_and_tag_and_semantics(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?category=food&tag=food ANDs both filters; only tx_a1 matches."""
        body = self.get_spend_response(
            monkeypatch, tmp_path, {"category": "food", "tag": "food"}
        )
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_TAG_FOOD_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_TAG_FOOD_COUNT

    def test_account_id_and_category_and_semantics(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?account_id=acct_alice&category=food ANDs both filters."""
        body = self.get_spend_response(
            monkeypatch,
            tmp_path,
            {"account_id": "acct_alice", "category": "food"},
        )
        assert body["total_spend"] == pytest.approx(
            _SPEND_JAN_ACCT_ALICE_TOTAL
        )
        assert body["transaction_count"] == _SPEND_JAN_ACCT_ALICE_COUNT

    def test_no_new_filters_unchanged_behavior(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Omitting new filters does not change existing behavior."""
        body = self.get_spend_response(monkeypatch, tmp_path, {})
        assert body["total_spend"] == pytest.approx(_SPEND_JAN_CANONICAL_TOTAL)
        assert body["transaction_count"] == _SPEND_JAN_CANONICAL_COUNT

    def test_filters_field_always_includes_new_keys(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Response filters dict always includes account_id, category, tag."""
        body = self.get_spend_response(monkeypatch, tmp_path, {})
        filters = cast("dict[str, object]", body["filters"])
        assert "account_id" in filters
        assert "category" in filters
        assert "tag" in filters
        assert filters["account_id"] is None
        assert filters["category"] is None
        assert filters["tag"] is None

    def test_filters_field_reflects_supplied_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Response filters dict echoes back the supplied filter values."""
        body = self.get_spend_response(
            monkeypatch,
            tmp_path,
            {
                "account_id": "acct_alice",
                "category": "food",
                "tag": "groceries",
            },
        )
        filters = cast("dict[str, object]", body["filters"])
        assert filters["account_id"] == "acct_alice"
        assert filters["category"] == "food"
        assert filters["tag"] == "groceries"
