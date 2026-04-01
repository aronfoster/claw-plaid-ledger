"""Tests for the GET /spend/trends endpoint."""

from __future__ import annotations

import http
import sqlite3
from datetime import date
from typing import TYPE_CHECKING

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

# Fixed "today" used in all trends tests.
_TRENDS_TODAY = "2026-03-15"
# Expected bucket counts for the default and narrow windows.
_TRENDS_DEFAULT_MONTHS = 6
_TRENDS_THREE_MONTHS = 3


def _patch_today(monkeypatch: pytest.MonkeyPatch, isodate: str) -> None:
    """Patch claw_plaid_ledger.routers.spend._today to return *isodate*."""
    fixed = date.fromisoformat(isodate)
    monkeypatch.setattr(
        "claw_plaid_ledger.routers.spend._today", lambda: fixed
    )


def _seed_trends_data(db_path: pathlib.Path) -> None:
    """
    Seed fixture data for GET /spend/trends tests.

    Accounts:
      acct_alice  owner=alice   canonical (not suppressed)
      acct_bob    owner=bob     canonical
      acct_dup    owner=alice   suppressed (canonical_account_id=acct_alice)

    Transactions (non-pending unless noted), with today=2026-03-15:
      tx_t1  acct_alice  100.0  posted 2025-12-10   (alice, dec-2025)
      tx_t2  acct_bob    200.0  posted 2025-12-20   (bob,   dec-2025)
      tx_t3  acct_alice  150.0  posted 2026-01-10   (alice, jan-2026)
      tx_t4  acct_alice  80.0   posted 2026-02-05   (alice, feb-2026)
                category=Software  tags=["software","subscription"]
      tx_t5  acct_bob    300.0  posted 2026-02-15   (bob, feb-2026)
      tx_t6  acct_alice   50.0  posted 2026-03-05   (alice, mar-2026 partial)
      tx_tp  acct_alice   75.0  PENDING auth 2026-03-10  (alice, pending)
      tx_dup acct_dup    999.0  posted 2026-01-15   (suppressed)

    Months 2025-10 and 2025-11 have no transactions (zero-fill path).

    Canonical non-pending totals by month (for months=6 window):
      2025-10:  0.0 (zero-fill)
      2025-11:  0.0 (zero-fill)
      2025-12: 300.0 (tx_t1+tx_t2)
      2026-01: 150.0 (tx_t3; tx_dup is suppressed)
      2026-02: 380.0 (tx_t4+tx_t5)
      2026-03:  50.0 (tx_t6; tx_tp is pending)
      Grand total (GET /spend): 880.0
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
                    "tx_t1",
                    "acct_alice",
                    100.0,
                    "USD",
                    "Store A",
                    None,
                    0,
                    None,
                    "2025-12-10",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t2",
                    "acct_bob",
                    200.0,
                    "USD",
                    "Store B",
                    None,
                    0,
                    None,
                    "2025-12-20",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t3",
                    "acct_alice",
                    150.0,
                    "USD",
                    "Store C",
                    None,
                    0,
                    None,
                    "2026-01-10",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t4",
                    "acct_alice",
                    80.0,
                    "USD",
                    "Software Co",
                    None,
                    0,
                    None,
                    "2026-02-05",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t5",
                    "acct_bob",
                    300.0,
                    "USD",
                    "Transit Co",
                    None,
                    0,
                    None,
                    "2026-02-15",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t6",
                    "acct_alice",
                    50.0,
                    "USD",
                    "Coffee Shop",
                    None,
                    0,
                    None,
                    "2026-03-05",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_tp",
                    "acct_alice",
                    75.0,
                    "USD",
                    "Pending Charge",
                    None,
                    1,
                    "2026-03-10",
                    None,
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
                    "2026-01-15",
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
            ],
        )
        connection.executemany(
            (
                "INSERT INTO allocations (plaid_transaction_id, amount, "
                "category, note, tags, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    "tx_t1",
                    100.0,
                    None,
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t2",
                    200.0,
                    None,
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t3",
                    150.0,
                    None,
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t4",
                    80.0,
                    "Software",
                    None,
                    '["software","subscription"]',
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t5",
                    300.0,
                    None,
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_t6",
                    50.0,
                    None,
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_tp",
                    75.0,
                    None,
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
                (
                    "tx_dup",
                    999.0,
                    None,
                    None,
                    None,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
            ],
        )


class TestGetSpendTrendsEndpoint:
    """Tests for GET /spend/trends endpoint behavior."""

    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _seed_trends_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        _patch_today(monkeypatch, _TRENDS_TODAY)

    # --- Shape and ordering ---

    def test_default_months_returns_six_buckets(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Default ?months=6 returns exactly 6 buckets."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
        data = response.json()
        assert len(data) == _TRENDS_DEFAULT_MONTHS

    def test_buckets_ordered_oldest_to_newest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Buckets are returned oldest → newest."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        months = [b["month"] for b in response.json()]
        assert months == sorted(months)
        assert months[0] == "2025-10"
        assert months[-1] == "2026-03"

    def test_only_current_month_is_partial(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Only the last bucket (2026-03) has partial: true."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        buckets = response.json()
        for b in buckets[:-1]:
            assert b["partial"] is False, (
                f"Expected partial=false for {b['month']}"
            )
        assert buckets[-1]["partial"] is True

    def test_months_1_returns_single_partial_bucket(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?months=1 returns a single bucket with partial: true."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"months": 1},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        data = response.json()
        assert len(data) == 1
        assert data[0]["month"] == "2026-03"
        assert data[0]["partial"] is True

    def test_months_3_returns_three_buckets(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?months=3 returns exactly 3 buckets."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"months": 3},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        data = response.json()
        assert len(data) == _TRENDS_THREE_MONTHS
        assert data[0]["month"] == "2026-01"
        assert data[-1]["month"] == "2026-03"

    # --- Zero-fill ---

    def test_zero_fill_months_with_no_transactions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Months with no transactions appear with zeroes."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        buckets = {b["month"]: b for b in response.json()}
        assert buckets["2025-10"]["total_spend"] == 0.0
        assert buckets["2025-10"]["allocation_count"] == 0
        assert buckets["2025-11"]["total_spend"] == 0.0
        assert buckets["2025-11"]["allocation_count"] == 0

    # --- Totals sanity ---

    def test_sum_equals_spend_endpoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Sum of total_spend across all buckets matches GET /spend."""
        self._setup(monkeypatch, tmp_path)
        trends_resp = client.get(
            "/spend/trends",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        trends_total = sum(b["total_spend"] for b in trends_resp.json())

        spend_resp = client.get(
            "/spend",
            params={"start_date": "2025-10-01", "end_date": "2026-03-15"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        spend_total = spend_resp.json()["total_spend"]

        assert trends_total == pytest.approx(spend_total)

    # --- Filter parity ---

    def test_filter_owner_alice(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?owner=alice includes only alice's transactions in each bucket."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"owner": "alice"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        buckets = {b["month"]: b for b in response.json()}
        # Dec 2025: only tx_t1 (alice=100), not tx_t2 (bob=200)
        assert buckets["2025-12"]["total_spend"] == pytest.approx(100.0)
        assert buckets["2025-12"]["allocation_count"] == 1
        # Feb 2026: only tx_t4 (alice=80), not tx_t5 (bob=300)
        assert buckets["2026-02"]["total_spend"] == pytest.approx(80.0)

    def test_filter_account_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?account_id=acct_bob only counts bob's account transactions."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"account_id": "acct_bob"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        buckets = {b["month"]: b for b in response.json()}
        assert buckets["2025-12"]["total_spend"] == pytest.approx(200.0)
        assert buckets["2025-12"]["allocation_count"] == 1
        assert buckets["2026-01"]["total_spend"] == 0.0
        assert buckets["2026-01"]["allocation_count"] == 0

    def test_filter_category_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?category=software matches annotation (case-insensitive)."""
        self._setup(monkeypatch, tmp_path)
        for cat_val in ("Software", "software", "SOFTWARE"):
            response = client.get(
                "/spend/trends",
                params={"category": cat_val},
                headers={"Authorization": f"Bearer {_TOKEN}"},
            )
            buckets = {b["month"]: b for b in response.json()}
            assert buckets["2026-02"]["total_spend"] == pytest.approx(80.0), (
                f"category={cat_val!r} should match tx_t4"
            )
            assert buckets["2026-02"]["allocation_count"] == 1

    def test_filter_tag_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tag=subscription matches annotation tag (case-insensitive)."""
        self._setup(monkeypatch, tmp_path)
        for tag_val in ("subscription", "Subscription", "SUBSCRIPTION"):
            response = client.get(
                "/spend/trends",
                params={"tag": tag_val},
                headers={"Authorization": f"Bearer {_TOKEN}"},
            )
            buckets = {b["month"]: b for b in response.json()}
            assert buckets["2026-02"]["total_spend"] == pytest.approx(80.0), (
                f"tag={tag_val!r} should match tx_t4"
            )

    def test_filter_tags_and_semantics(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?tags=a&tags=b — AND semantics: only transactions with both tags."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params=[("tags", "software"), ("tags", "subscription")],
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        buckets = {b["month"]: b for b in response.json()}
        assert buckets["2026-02"]["total_spend"] == pytest.approx(80.0)
        assert buckets["2026-02"]["allocation_count"] == 1

    def test_filter_view_raw_includes_suppressed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?view=raw includes suppressed account transactions."""
        self._setup(monkeypatch, tmp_path)
        canonical_resp = client.get(
            "/spend/trends",
            params={"months": 6},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        raw_resp = client.get(
            "/spend/trends",
            params={"months": 6, "view": "raw"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        canonical_total = sum(b["total_spend"] for b in canonical_resp.json())
        raw_total = sum(b["total_spend"] for b in raw_resp.json())
        # raw includes tx_dup (999.0) which canonical excludes
        assert raw_total > canonical_total
        raw_jan = next(b for b in raw_resp.json() if b["month"] == "2026-01")
        assert raw_jan["total_spend"] == pytest.approx(150.0 + 999.0)

    def test_filter_include_pending(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?include_pending=true counts pending; without it excluded."""
        self._setup(monkeypatch, tmp_path)
        no_pending = client.get(
            "/spend/trends",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        with_pending = client.get(
            "/spend/trends",
            params={"include_pending": "true"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        no_pending_mar = next(
            b for b in no_pending.json() if b["month"] == "2026-03"
        )
        with_pending_mar = next(
            b for b in with_pending.json() if b["month"] == "2026-03"
        )
        assert no_pending_mar["total_spend"] == pytest.approx(50.0)
        assert with_pending_mar["total_spend"] == pytest.approx(50.0 + 75.0)

    # --- Validation ---

    def test_months_zero_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?months=0 returns HTTP 422 (below minimum)."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"months": 0},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_months_negative_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """?months=-1 returns HTTP 422."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"months": -1},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    # --- Auth ---

    def test_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Request without Authorization header returns HTTP 401."""
        self._setup(monkeypatch, tmp_path)
        response = client.get("/spend/trends")
        assert response.status_code == http.HTTPStatus.UNAUTHORIZED


# ---------------------------------------------------------------------------
# Tests for GET /spend/trends — BUG-014 strict query parameter checking
# ---------------------------------------------------------------------------


class TestGetSpendTrendsStrictParams:
    """BUG-014: unknown query params on GET /spend/trends return HTTP 422."""

    def _setup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _seed_trends_data(db_path)
        monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        _patch_today(monkeypatch, _TRENDS_TODAY)

    def test_misspelled_param_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Misspelled query parameter returns HTTP 422."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"moth": 3},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY

    def test_unknown_param_body_contains_unrecognized_and_valid_parameters(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """422 body contains 'unrecognized' and 'valid_parameters' keys."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={"moth": 3},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
        detail = response.json()["detail"]
        assert "unrecognized" in detail
        assert "valid_parameters" in detail
        assert "moth" in detail["unrecognized"]

    def test_all_valid_params_returns_200(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Request with all valid parameters is unaffected (no regression)."""
        self._setup(monkeypatch, tmp_path)
        response = client.get(
            "/spend/trends",
            params={
                "months": 3,
                "owner": "alice",
                "include_pending": "false",
                "view": "canonical",
                "account_id": "acct_alice",
                "category": "Software",
                "tag": "software",
            },
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == http.HTTPStatus.OK
