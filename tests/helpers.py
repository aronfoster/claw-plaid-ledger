"""Shared test helper functions (not fixtures)."""

from __future__ import annotations

import re
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.cli import main
from claw_plaid_ledger.db import initialize_database

if TYPE_CHECKING:
    import pathlib

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------

# Used by test_server_transactions.py and test_server_annotations.py.
def _seed_transactions(db_path: pathlib.Path) -> None:
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
                (
                    "acct_2",
                    "Account 2",
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


# ---------------------------------------------------------------------------
# Shared date-range helpers
# ---------------------------------------------------------------------------

# Fixed "today" used in all date-range tests: 2026-03-21 (a Saturday)
# Derived windows relative to _RANGE_TODAY:
#   this_month:   2026-03-01 .. 2026-03-21
#   last_month:   2026-02-01 .. 2026-02-28  (Feb 2026 is not a leap year)
#   last_30_days: 2025-12-21 .. 2026-03-21  (30 days back from 2026-03-21)
#   last_7_days:  2026-03-14 .. 2026-03-21
# Used by test_server_transactions.py and test_server_spend.py.
_RANGE_TODAY = "2026-03-21"


def _patch_today(monkeypatch: pytest.MonkeyPatch, isodate: str) -> None:
    """Patch claw_plaid_ledger.server._today to return *isodate*."""
    fixed = date.fromisoformat(isodate)
    monkeypatch.setattr("claw_plaid_ledger.server._today", lambda: fixed)


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


def run_main(args: list[str]) -> tuple[int, str]:
    """Run CLI main with custom argv and return exit code and stdout text."""
    old_argv = sys.argv
    buffer = StringIO()

    try:
        sys.argv = ["ledger", *args]
        with redirect_stdout(buffer):
            try:
                main()
                exit_code = 0
            except SystemExit as exc:
                exit_code = 0 if exc.code is None else int(exc.code)
    finally:
        sys.argv = old_argv

    return exit_code, _ANSI_ESCAPE.sub("", buffer.getvalue())
