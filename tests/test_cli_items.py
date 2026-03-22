"""CLI items, apply-precedence, and overlaps tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from claw_plaid_ledger.db import initialize_database, upsert_sync_state
from claw_plaid_ledger.items_config import (
    ItemConfig,
    ItemsConfigError,
    SuppressedAccountConfig,
)
from tests.helpers import run_main

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


# ---------------------------------------------------------------------------
# ledger items tests
# ---------------------------------------------------------------------------


def test_items_no_items_configured(monkeypatch: MonkeyPatch) -> None:
    """`items` exits 0 and reports no items when items.toml is absent."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "no items configured" in output


def test_items_parse_error_exits_1(monkeypatch: MonkeyPatch) -> None:
    """`items` exits 1 and reports the error when items.toml is invalid."""

    def raise_items_error() -> list[ItemConfig]:
        message = "items[0] missing required field 'id'"
        raise ItemsConfigError(message)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config", raise_items_error
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 1
    assert "items: parse error:" in output
    assert "items[0] missing required field 'id'" in output


def test_items_mixed_tokens_set_and_missing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`items` shows SET/MISSING per token and a correct summary line."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    bob_env = "PLAID_ACCESS_TOKEN_CARD_BOB"
    monkeypatch.setenv(alice_env, "access-sandbox-token-alice")
    monkeypatch.delenv(bob_env, raising=False)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
            ),
            ItemConfig(
                id="card-bob",
                access_token_env=bob_env,
                owner="bob",
            ),
        ],
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "items: bank-alice owner=alice token=SET" in output
    assert "items: card-bob owner=bob token=MISSING" in output
    assert "items: 1/2 items healthy, 1 need attention" in output


def test_items_shows_account_and_sync_counts(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`items` shows correct account and sync-state counts from the DB."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, item_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "acct-1",
                "Checking",
                "bank-alice",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, item_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "acct-2",
                "Savings",
                "bank-alice",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        upsert_sync_state(
            conn,
            item_id="bank-alice",
            cursor="cursor-1",
            owner="alice",
            last_synced_at="2026-03-10T14:22:00+00:00",
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.setenv(alice_env, "access-sandbox-token-alice")

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
            ),
        ],
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "accounts=2" in output
    assert "last_synced=2026-03-10T14:22:00+00:00" in output
    assert "items: 1/1 items healthy, 0 need attention" in output


def test_items_no_owner_shows_none_placeholder(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`items` shows (none) when owner is not set for an item."""
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    card_env = "PLAID_ACCESS_TOKEN_CARD_SHARED"
    monkeypatch.setenv(card_env, "access-token-shared")

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="card-shared",
                access_token_env=card_env,
                owner=None,
            ),
        ],
    )

    exit_code, output = run_main(["items"])

    assert exit_code == 0
    assert "owner=(none)" in output


# ---------------------------------------------------------------------------
# Task 2: apply-precedence command
# ---------------------------------------------------------------------------


def test_apply_precedence_no_suppressions_exits_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    """`apply-precedence` exits 0 when no aliases are configured."""
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
            )
        ],
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "no suppressions configured" in output


def test_apply_precedence_empty_items_config_exits_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    """`apply-precedence` exits 0 when items.toml has no items."""
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        list,
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "no suppressions configured" in output


def test_apply_precedence_updates_known_account(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`apply-precedence` updates an account that exists in the DB."""
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, type, created_at, updated_at) "
            "VALUES ('acct-suppressed', 'Test', 'credit', "
            "'2024-01-01', '2024-01-01')"
        )

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-suppressed",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "loaded 1 alias(es)" in output
    assert "updated 1 account(s)" in output
    assert "done" in output


def test_apply_precedence_skips_account_not_in_db(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`apply-precedence` reports skipped aliases for unsynced accounts."""
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    db_path = tmp_path / "ledger.db"
    initialize_database(db_path)
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-not-synced",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 0
    assert "1 alias(es) skipped" in output
    assert "sync first" in output


def test_apply_precedence_config_error_exits_one(
    monkeypatch: MonkeyPatch,
) -> None:
    """`apply-precedence` exits 1 on ItemsConfigError."""
    error_msg = "bad config"

    def _raise_config_error() -> list[ItemConfig]:
        raise ItemsConfigError(error_msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        _raise_config_error,
    )

    exit_code, output = run_main(["apply-precedence"])

    assert exit_code == 1
    assert "config error" in output


# ---------------------------------------------------------------------------
# Task 4: overlaps command
# ---------------------------------------------------------------------------


def test_overlaps_no_suppressions_exits_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    """`overlaps` exits 0 with a no-config message when empty."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "overlaps: no suppressions configured" in output


def test_overlaps_reports_in_db_status(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` shows IN DB when suppression matches DB state."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, mask, type, item_id, "
            "canonical_account_id, "
            "created_at, updated_at) "
            "VALUES ('acct-suppressed', 'Premium Rewards', '4321', "
            "'credit', 'bank-alice', 'acct-canonical', '2024-01-01', "
            "'2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-suppressed",
                        canonical_account_id="acct-canonical",
                        canonical_from_item="card-bob",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "Configured suppressions" in output
    assert "[IN DB]" in output
    assert "card-bob" in output
    assert "0 configured suppression active" not in output


def test_overlaps_reports_not_yet_synced_status(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` shows NOT YET SYNCED for unknown suppressed account."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-not-synced",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "[NOT YET SYNCED — run sync first]" in output
    assert "0 configured suppression active" in output
    assert "1 pending sync" in output


def test_overlaps_reports_mismatch_status(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` shows MISMATCH when DB canonical differs from config."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, type, item_id, canonical_account_id, "
            "created_at, updated_at) "
            "VALUES ('acct-suppressed', 'Premium Rewards', 'credit', "
            "'bank-alice', 'acct-old-canonical', '2024-01-01', "
            "'2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-suppressed",
                        canonical_account_id="acct-new-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "[MISMATCH]" in output
    assert "0 configured suppression active" in output


def test_overlaps_detects_potential_unconfirmed_overlaps(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` flags same name/mask/type accounts across items."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, mask, type, item_id, "
            "canonical_account_id, "
            "created_at, updated_at) VALUES "
            "('acct-shared-a', 'Premium Rewards', '4321', 'credit', "
            "'bank-alice', 'acct-canonical', '2024-01-01', '2024-01-01'), "
            "('acct-shared-b', 'Premium Rewards', '4321', 'credit', "
            "'card-bob', NULL, '2024-01-01', '2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-shared-a",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "Potential unconfirmed overlaps" in output
    assert '"Premium Rewards"  mask=4321  type=credit' in output
    assert "items: bank-alice, card-bob" in output
    assert "1 potential overlap flagged" in output


def test_overlaps_no_unconfirmed_overlaps_reports_none_detected(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`overlaps` prints none detected when no overlap candidates exist."""
    db_path = tmp_path / "ledger.db"
    alice_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO accounts "
            "(plaid_account_id, name, type, item_id, canonical_account_id, "
            "created_at, updated_at) "
            "VALUES ('acct-solo', 'Everyday Checking', 'depository', "
            "'bank-alice', 'acct-canonical', '2024-01-01', '2024-01-01')"
        )
        conn.commit()

    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=alice_env,
                owner="alice",
                suppressed_accounts=(
                    SuppressedAccountConfig(
                        plaid_account_id="acct-solo",
                        canonical_account_id="acct-canonical",
                    ),
                ),
            )
        ],
    )

    exit_code, output = run_main(["overlaps"])

    assert exit_code == 0
    assert "none detected" in output
