"""CLI init-db and sync tests."""

from __future__ import annotations

import os
import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING

from claw_plaid_ledger.items_config import ItemConfig
from tests.helpers import run_main

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

INIT_DB_CONFIG_ERROR_EXIT_CODE = 2


def test_init_db_success(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """`init-db` creates a SQLite file and reports success."""
    db_path = tmp_path / "data" / "ledger.db"
    monkeypatch.setenv("CLAW_PLAID_LEDGER_DB_PATH", str(db_path))

    exit_code, output = run_main(["init-db"])

    assert exit_code == 0
    assert db_path.exists()
    assert f"init-db: initialized database at {db_path}" in output


def test_init_db_missing_db_path() -> None:
    """`init-db` fails clearly when DB path is not configured."""
    original = os.environ.pop("CLAW_PLAID_LEDGER_DB_PATH", None)

    try:
        exit_code, output = run_main(["init-db"])
    finally:
        if original is not None:
            os.environ["CLAW_PLAID_LEDGER_DB_PATH"] = original

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "init-db: Missing required environment variable(s):" in output


def test_sync_missing_plaid_config(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`sync` fails clearly when Plaid env vars are missing."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    monkeypatch.delenv("PLAID_ENV", raising=False)
    monkeypatch.delenv("PLAID_ACCESS_TOKEN", raising=False)

    exit_code, output = run_main(["sync"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync: Missing required environment variable(s):" in output


def test_sync_success_calls_engine(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`sync` invokes adapter + sync engine and prints a concise summary."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    access_token = secrets.token_urlsafe(12)
    monkeypatch.setenv("PLAID_ACCESS_TOKEN", access_token)

    class DummyAdapter:
        pass

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        assert kwargs["access_token"] == access_token
        assert str(kwargs["db_path"]).endswith("ledger.db")
        assert isinstance(kwargs["adapter"], DummyAdapter)
        return SimpleNamespace(
            added=2,
            modified=1,
            removed=0,
            accounts=3,
            next_cursor="cursor-1",
        )

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync"])

    assert exit_code == 0
    assert "sync: accounts=3 added=2 modified=1 removed=0" in output


def test_sync_item_success_calls_engine_with_item_owner(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --item` uses items.toml id, token env var, and owner."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.delenv("PLAID_ACCESS_TOKEN", raising=False)
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    access_token_alice = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_alice, access_token_alice)

    class DummyAdapter:
        pass

    called: dict[str, object] = {}

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        called.update(kwargs)
        return SimpleNamespace(
            added=2,
            modified=1,
            removed=0,
            accounts=3,
            next_cursor="cursor-1",
        )

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
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync", "--item", "bank-alice"])

    assert exit_code == 0
    assert called["item_id"] == "bank-alice"
    assert called["owner"] == "alice"
    assert called["access_token"] == access_token_alice
    assert (
        "sync[bank-alice]: accounts=3 added=2 modified=1 removed=0" in output
    )


def test_sync_item_missing_id_exits_2(monkeypatch: MonkeyPatch) -> None:
    """`sync --item` exits 2 when the requested item id is missing."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["sync", "--item", "missing-id"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync: item 'missing-id' not found in items.toml" in output


def test_sync_item_missing_token_env_exits_2(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --item` exits 2 when the configured token env var is absent."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
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

    exit_code, output = run_main(["sync", "--item", "bank-alice"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert f"{env_var_alice} is not set" in output


def test_sync_all_success_runs_all_items(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --all` runs each configured item and exits zero on success."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    access_token_alice = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_alice, access_token_alice)
    env_var_bob = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setenv(env_var_bob, secrets.token_urlsafe(12))

    class DummyAdapter:
        pass

    calls: list[str] = []

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        calls.append(str(kwargs["item_id"]))
        return SimpleNamespace(
            added=1,
            modified=0,
            removed=0,
            accounts=1,
            next_cursor="cursor-1",
        )

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
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync", "--all"])

    assert exit_code == 0
    assert calls == ["bank-alice", "bank-bob"]
    assert (
        "sync[bank-alice]: accounts=1 added=1 modified=0 removed=0" in output
    )
    assert "sync[bank-bob]: accounts=1 added=1 modified=0 removed=0" in output
    assert "sync --all: 2 items synced, 0 failed" in output


def test_sync_all_continues_when_one_item_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`sync --all` continues after one item error and exits with code 1."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    env_var_alice = "PLAID_ACCESS_TOKEN_BANK_ALICE"
    access_token_alice = secrets.token_urlsafe(12)
    monkeypatch.setenv(env_var_alice, access_token_alice)
    env_var_bob = "PLAID_ACCESS_TOKEN_BANK_BOB"
    monkeypatch.setenv(env_var_bob, secrets.token_urlsafe(12))

    class DummyAdapter:
        pass

    calls: list[str] = []

    def fake_from_config(_config: object) -> DummyAdapter:
        return DummyAdapter()

    def fake_run_sync(**kwargs: object) -> object:
        item_id = str(kwargs["item_id"])
        calls.append(item_id)
        if item_id == "bank-bob":
            message = "boom"
            raise RuntimeError(message)
        return SimpleNamespace(
            added=1,
            modified=0,
            removed=0,
            accounts=1,
            next_cursor="cursor-1",
        )

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
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        fake_from_config,
    )
    monkeypatch.setattr("claw_plaid_ledger.cli.run_sync", fake_run_sync)

    exit_code, output = run_main(["sync", "--all"])

    assert exit_code == 1
    assert calls == ["bank-alice", "bank-bob"]
    assert (
        "sync[bank-alice]: accounts=1 added=1 modified=0 removed=0" in output
    )
    assert "sync[bank-bob]: ERROR boom" in output
    assert "sync --all: 1 items synced, 1 failed" in output


def test_sync_all_exits_2_when_items_empty(monkeypatch: MonkeyPatch) -> None:
    """`sync --all` exits 2 when items.toml has no items."""
    monkeypatch.setattr("claw_plaid_ledger.cli.load_items_config", list)

    exit_code, output = run_main(["sync", "--all"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync --all: no items found in items.toml" in output


def test_sync_item_and_all_are_mutually_exclusive() -> None:
    """`sync --item` and `--all` together exits with code 2."""
    exit_code, output = run_main(["sync", "--item", "foo", "--all"])

    assert exit_code == INIT_DB_CONFIG_ERROR_EXIT_CODE
    assert "sync: --item and --all are mutually exclusive" in output
