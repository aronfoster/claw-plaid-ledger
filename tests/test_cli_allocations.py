"""Tests for `ledger allocations` show and set commands."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from tests.helpers import run_main

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

# Short name so S105 does not fire; carries no real security significance.
_TOKEN = "test-bearer-value"  # noqa: S105

_TX_DETAIL_ONE_ALLOC: dict[str, object] = {
    "id": "tx_abc",
    "amount": 100.0,
    "name": "AMAZON.COM",
    "posted_date": "2026-03-15",
    "authorized_date": None,
    "allocations": [
        {
            "id": 5,
            "amount": 100.0,
            "category": "groceries",
            "tags": ["household"],
            "note": "weekly shopping",
            "updated_at": "2026-03-15T10:00:00+00:00",
        }
    ],
}

_TX_DETAIL_TWO_ALLOC: dict[str, object] = {
    "id": "tx_abc",
    "amount": 100.0,
    "name": "AMAZON.COM",
    "posted_date": "2026-03-15",
    "authorized_date": None,
    "allocations": [
        {
            "id": 7,
            "amount": 60.0,
            "category": "groceries",
            "tags": ["household"],
            "note": "food",
            "updated_at": "2026-03-15T10:00:00+00:00",
        },
        {
            "id": 8,
            "amount": 40.0,
            "category": "household",
            "tags": None,
            "note": None,
            "updated_at": "2026-03-15T10:00:00+00:00",
        },
    ],
}

_TX_DETAIL_UNBALANCED: dict[str, object] = {
    "id": "tx_abc",
    "amount": 100.0,
    "name": "STORE",
    "posted_date": "2026-03-15",
    "authorized_date": None,
    "allocations": [
        {
            "id": 9,
            "amount": 99.0,
            "category": None,
            "tags": None,
            "note": None,
            "updated_at": "2026-03-15T10:00:00+00:00",
        }
    ],
}


def _mock_response(status_code: int, data: object) -> MagicMock:
    """Build a mock httpx response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    mock.text = json.dumps(data)
    return mock


def _patch_get(mock_cls: MagicMock, response: MagicMock) -> None:
    """Wire *response* as the return value of the mocked GET call."""
    mock_cls.return_value.__enter__.return_value.get.return_value = response


def _patch_put(mock_cls: MagicMock, response: MagicMock) -> None:
    """Wire *response* as the return value of the mocked PUT call."""
    mock_cls.return_value.__enter__.return_value.put.return_value = response


# ---------------------------------------------------------------------------
# allocations show
# ---------------------------------------------------------------------------


class TestAllocationsShow:
    """Tests for `ledger allocations show <transaction_id>`."""

    def test_show_single_allocation(self, monkeypatch: MonkeyPatch) -> None:
        """Formats a single-allocation transaction correctly."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        mock_resp = _mock_response(200, _TX_DETAIL_ONE_ALLOC)
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_get(mock_cls, mock_resp)
            exit_code, output = run_main(["allocations", "show", "tx_abc"])

        assert exit_code == 0
        assert "Transaction: tx_abc" in output
        assert "Date:     2026-03-15" in output
        assert "Merchant: AMAZON.COM" in output
        assert "Amount:   $100.00" in output
        assert "Allocations (1):" in output
        assert "$100.00" in output
        assert "groceries" in output
        assert "[household]" in output
        assert "weekly shopping" in output
        assert "Total: $100.00" in output
        assert "\u2713 Balanced" in output

    def test_show_two_allocations(self, monkeypatch: MonkeyPatch) -> None:
        """Formats a two-allocation split transaction correctly."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        mock_resp = _mock_response(200, _TX_DETAIL_TWO_ALLOC)
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_get(mock_cls, mock_resp)
            exit_code, output = run_main(["allocations", "show", "tx_abc"])

        assert exit_code == 0
        assert "Allocations (2):" in output
        assert "#1" in output
        assert "$60.00" in output
        assert "groceries" in output
        assert "[household]" in output
        assert "food" in output
        assert "#2" in output
        assert "$40.00" in output
        assert "household" in output
        assert "(no tags)" in output
        assert "(no note)" in output
        assert "Total: $100.00" in output
        assert "\u2713 Balanced" in output

    def test_show_unbalanced_warning(self, monkeypatch: MonkeyPatch) -> None:
        """Displays an unbalanced warning when totals differ."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        mock_resp = _mock_response(200, _TX_DETAIL_UNBALANCED)
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_get(mock_cls, mock_resp)
            exit_code, output = run_main(["allocations", "show", "tx_abc"])

        assert exit_code == 0
        assert "Total: $99.00" in output
        assert "\u26a0 Unbalanced" in output
        assert "$1.00" in output

    def test_show_404_exits_nonzero(self, monkeypatch: MonkeyPatch) -> None:
        """Exits non-zero and prints a clear message on 404."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)

        mock_resp = _mock_response(404, {"detail": "Transaction not found"})
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_get(mock_cls, mock_resp)
            exit_code, output = run_main(["allocations", "show", "tx_missing"])

        assert exit_code != 0
        assert "Transaction not found: tx_missing" in output

    def test_show_401_exits_nonzero(self, monkeypatch: MonkeyPatch) -> None:
        """Exits non-zero and prints auth message on 401."""
        monkeypatch.setenv("CLAW_API_SECRET", "wrong-token")

        mock_resp = _mock_response(401, {"detail": "Unauthorized"})
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_get(mock_cls, mock_resp)
            exit_code, output = run_main(["allocations", "show", "tx_abc"])

        assert exit_code != 0
        assert "Authentication failed" in output
        assert "CLAW_API_SECRET" in output


# ---------------------------------------------------------------------------
# allocations set
# ---------------------------------------------------------------------------


class TestAllocationsSet:
    """Tests for `ledger allocations set <transaction_id> --file <path>`."""

    def test_set_reads_file_and_calls_put(
        self, monkeypatch: MonkeyPatch, tmp_path: Path
    ) -> None:
        """Reads a JSON file and renders the successful response."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        alloc_file = tmp_path / "allocs.json"
        alloc_file.write_text(
            json.dumps(
                [
                    {
                        "amount": 60.0,
                        "category": "groceries",
                        "tags": ["household"],
                        "note": "food",
                    },
                    {"amount": 40.0, "category": "household"},
                ]
            ),
            encoding="utf-8",
        )

        mock_resp = _mock_response(200, _TX_DETAIL_TWO_ALLOC)
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            mock_put = mock_cls.return_value.__enter__.return_value.put
            mock_put.return_value = mock_resp
            exit_code, output = run_main(
                ["allocations", "set", "tx_abc", "--file", str(alloc_file)]
            )

        assert exit_code == 0
        assert "Transaction: tx_abc" in output
        assert "Allocations (2):" in output
        assert "\u2713 Balanced" in output
        # Verify the PUT was called with the correct URL.
        assert "/transactions/tx_abc/allocations" in mock_put.call_args[0][0]

    def test_set_reads_from_stdin(self, monkeypatch: MonkeyPatch) -> None:
        """Reads allocation JSON from stdin when --file is -."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        stdin_data = json.dumps([{"amount": 100.0, "category": "groceries"}])

        mock_resp = _mock_response(200, _TX_DETAIL_ONE_ALLOC)
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_put(mock_cls, mock_resp)
            with patch("claw_plaid_ledger.cli.sys.stdin") as mock_stdin:
                mock_stdin.read.return_value = stdin_data
                exit_code, output = run_main(
                    ["allocations", "set", "tx_abc", "--file", "-"]
                )

        assert exit_code == 0
        assert "Transaction: tx_abc" in output

    def test_set_422_balance_error_prints_amounts(
        self, monkeypatch: MonkeyPatch, tmp_path: Path
    ) -> None:
        """Prints 422 validation detail without traceback."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        alloc_file = tmp_path / "allocs.json"
        alloc_file.write_text(
            json.dumps([{"amount": 50.0}]),
            encoding="utf-8",
        )
        error_body = {
            "detail": {
                "error": "allocation amounts do not balance",
                "transaction_amount": 100.0,
                "allocation_total": 50.0,
                "difference": 50.0,
            }
        }

        mock_resp = _mock_response(422, error_body)
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_put(mock_cls, mock_resp)
            exit_code, output = run_main(
                ["allocations", "set", "tx_abc", "--file", str(alloc_file)]
            )

        assert exit_code != 0
        assert "do not balance" in output
        assert "transaction_amount" in output
        assert "$100.00" in output
        assert "$50.00" in output
        # No traceback.
        assert "Traceback" not in output

    def test_set_404_exits_nonzero(
        self, monkeypatch: MonkeyPatch, tmp_path: Path
    ) -> None:
        """Exits non-zero with a clear message on 404."""
        monkeypatch.setenv("CLAW_API_SECRET", _TOKEN)
        alloc_file = tmp_path / "allocs.json"
        alloc_file.write_text(
            json.dumps([{"amount": 100.0}]),
            encoding="utf-8",
        )

        mock_resp = _mock_response(404, {"detail": "Transaction not found"})
        with patch("claw_plaid_ledger.cli.httpx.Client") as mock_cls:
            _patch_put(mock_cls, mock_resp)
            exit_code, output = run_main(
                [
                    "allocations",
                    "set",
                    "tx_missing",
                    "--file",
                    str(alloc_file),
                ]
            )

        assert exit_code != 0
        assert "Transaction not found: tx_missing" in output
