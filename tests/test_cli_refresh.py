"""CLI refresh command tests."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from claw_plaid_ledger.items_config import ItemConfig
from claw_plaid_ledger.sync_engine import (
    PlaidPermanentError,
    PlaidTransientError,
)
from tests.helpers import run_main

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

CONFIG_ERROR_EXIT_CODE = 2


def test_refresh_default_success(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`refresh` succeeds with PLAID_ACCESS_TOKEN set."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    access_token = secrets.token_urlsafe(12)
    monkeypatch.setenv("PLAID_ACCESS_TOKEN", access_token)

    called_with: list[str] = []

    def fake_refresh_transactions(_self: object, token: str) -> None:
        called_with.append(token)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh"])

    assert exit_code == 0
    assert "refresh: OK" in output
    assert called_with == [access_token]


def test_refresh_default_missing_token(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`refresh` exits 2 when PLAID_ACCESS_TOKEN is not set."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.delenv("PLAID_ACCESS_TOKEN", raising=False)

    exit_code, output = run_main(["refresh"])

    assert exit_code == CONFIG_ERROR_EXIT_CODE
    assert "PLAID_ACCESS_TOKEN" in output


def test_refresh_default_permanent_error(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`refresh` exits 1 on PlaidPermanentError."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_ACCESS_TOKEN", secrets.token_urlsafe(12))

    def fake_refresh_transactions(_self: object, _token: str) -> None:
        msg = "bad token"
        raise PlaidPermanentError(msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh"])

    assert exit_code == 1
    assert "refresh: ERROR" in output


def test_refresh_default_transient_error(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`refresh` exits 1 on PlaidTransientError."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_ACCESS_TOKEN", secrets.token_urlsafe(12))

    def fake_refresh_transactions(_self: object, _token: str) -> None:
        msg = "rate limited"
        raise PlaidTransientError(msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh"])

    assert exit_code == 1
    assert "refresh: ERROR" in output


def test_refresh_item_success(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`refresh --item` succeeds for a known item."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    access_token_alice = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_alice, access_token_alice)
    monkeypatch.delenv("PLAID_ACCESS_TOKEN", raising=False)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            )
        ],
    )

    def fake_refresh_transactions(_self: object, _token: str) -> None:
        pass

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh", "--item", "bank-alice"])

    assert exit_code == 0
    assert "refresh[bank-alice]: OK" in output


def test_refresh_item_not_found(monkeypatch: MonkeyPatch) -> None:
    """`refresh --item` exits 2 when the item ID is unknown."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["refresh", "--item", "unknown-id"])

    assert exit_code == CONFIG_ERROR_EXIT_CODE
    assert "not found in items.toml" in output


def test_refresh_item_missing_token(monkeypatch: MonkeyPatch) -> None:
    """`refresh --item` exits 2 when the token env var is not set."""
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.delenv(env_var_alice, raising=False)
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            )
        ],
    )

    exit_code, _output = run_main(["refresh", "--item", "bank-alice"])

    assert exit_code == CONFIG_ERROR_EXIT_CODE


def test_refresh_item_adapter_error(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`refresh --item` exits 1 on adapter error."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    monkeypatch.setenv(env_var_alice, secrets.token_urlsafe(12))
    monkeypatch.delenv("PLAID_ACCESS_TOKEN", raising=False)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            )
        ],
    )

    def fake_refresh_transactions(_self: object, _token: str) -> None:
        msg = "bad token"
        raise PlaidPermanentError(msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh", "--item", "bank-alice"])

    assert exit_code == 1
    assert "refresh[bank-alice]: ERROR" in output


def test_refresh_all_success(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """`refresh --all` succeeds for all items and exits 0."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    env_var_bob = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setenv(env_var_alice, secrets.token_urlsafe(12))
    monkeypatch.setenv(env_var_bob, secrets.token_urlsafe(12))

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            ),
            ItemConfig(
                id="bank-bob",
                access_token_env=env_var_bob,
                owner="bob",
            ),
        ],
    )

    def fake_refresh_transactions(_self: object, _token: str) -> None:
        pass

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh", "--all"])

    assert exit_code == 0
    assert "refresh[bank-alice]: OK" in output
    assert "refresh[bank-bob]: OK" in output
    assert "2 items refreshed, 0 failed" in output


def test_refresh_all_partial_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`refresh --all` exits 1 when one item fails."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    env_var_bob = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setenv(env_var_alice, secrets.token_urlsafe(12))
    bob_token = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_bob, bob_token)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            ),
            ItemConfig(
                id="bank-bob",
                access_token_env=env_var_bob,
                owner="bob",
            ),
        ],
    )

    def fake_refresh_transactions(_self: object, token: str) -> None:
        if token == bob_token:
            msg = "transient failure"
            raise PlaidTransientError(msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh", "--all"])

    assert exit_code == 1
    assert "1 items refreshed, 1 failed" in output


def test_refresh_all_missing_token_counted_as_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`refresh --all` counts missing token as failure and exits 1."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    env_var_bob = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setenv(env_var_alice, secrets.token_urlsafe(12))
    monkeypatch.delenv(env_var_bob, raising=False)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.load_items_config",
        lambda: [
            ItemConfig(
                id="bank-alice",
                access_token_env=env_var_alice,
                owner="alice",
            ),
            ItemConfig(
                id="bank-bob",
                access_token_env=env_var_bob,
                owner="bob",
            ),
        ],
    )

    def fake_refresh_transactions(_self: object, _token: str) -> None:
        pass

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.refresh_transactions",
        fake_refresh_transactions,
    )

    exit_code, output = run_main(["refresh", "--all"])

    assert exit_code == 1
    assert "1 failed" in output


def test_refresh_all_no_items(monkeypatch: MonkeyPatch) -> None:
    """`refresh --all` exits 2 when no items are configured."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["refresh", "--all"])

    assert exit_code == CONFIG_ERROR_EXIT_CODE
    assert "no items found in items.toml" in output


def test_refresh_item_and_all_mutually_exclusive() -> None:
    """`refresh --item` and `--all` together exits 2."""
    exit_code, output = run_main(["refresh", "--item", "foo", "--all"])

    assert exit_code == CONFIG_ERROR_EXIT_CODE
    assert "mutually exclusive" in output
