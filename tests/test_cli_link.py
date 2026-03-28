"""CLI link tests."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from tests.helpers import run_main

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

_LINK_CONFIG_ERROR_EXIT_CODE = 2


class _FakeServer:
    """Minimal stand-in for http.server.HTTPServer used in link tests."""

    def shutdown(self) -> None:
        """No-op shutdown."""


def test_link_missing_plaid_config_exits_2(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` exits 2 when required Plaid env vars are absent."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    monkeypatch.delenv("PLAID_ENV", raising=False)

    exit_code, output = run_main(["link"])

    assert exit_code == _LINK_CONFIG_ERROR_EXIT_CODE
    assert "link: Missing required environment variable(s):" in output


def test_link_create_token_error_exits_1(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` exits 1 when create_link_token raises."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    class _ErrorAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            _products: list[str],
            _country_codes: list[str],
            **_kwargs: object,
        ) -> str:
            msg = "Plaid permanent API error (HTTP 400): ..."
            raise RuntimeError(msg)

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _ErrorAdapter(),
    )

    exit_code, output = run_main(["link"])

    assert exit_code == 1
    assert "link: failed to create link token" in output


def test_link_success_prints_access_token(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` end-to-end: creates token, receives callback, exchanges."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    # Pre-set done_event so CLI `done_event.wait()` returns immediately.
    fake_done = threading.Event()
    fake_result: list[str] = ["public-sandbox-test-token"]
    fake_done.set()

    class _SuccessAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            _products: list[str],
            _country_codes: list[str],
            **_kwargs: object,
        ) -> str:
            return "link-sandbox-fake"

        def exchange_public_token(self, public_token: str) -> tuple[str, str]:
            assert public_token == "public-sandbox-test-token"  # noqa: S105
            return "access-sandbox-fake-token", "item-abc"

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _SuccessAdapter(),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.start_link_server",
        lambda _tok: (_FakeServer(), fake_done, fake_result),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.webbrowser.open", lambda _url: None
    )

    exit_code, output = run_main(["link"])

    assert exit_code == 0
    assert "access-sandbox-fake-token" in output
    assert "item-abc" in output
    assert "items.toml" in output


def test_link_default_product_is_transactions(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` with no --products flag defaults to transactions."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    captured_products: list[list[str]] = []
    fake_done = threading.Event()
    fake_result: list[str] = ["public-tok"]
    fake_done.set()

    class _CaptureAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            products: list[str],
            _country_codes: list[str],
            **_kwargs: object,
        ) -> str:
            captured_products.append(products)
            return "link-sandbox-fake"

        def exchange_public_token(self, _public_token: str) -> tuple[str, str]:
            return "access-tok", "item-id"

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _CaptureAdapter(),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.start_link_server",
        lambda _tok: (_FakeServer(), fake_done, fake_result),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.webbrowser.open", lambda _url: None
    )

    exit_code, _output = run_main(["link"])

    assert exit_code == 0
    assert captured_products == [["transactions"]]


def test_link_keyboard_interrupt_exits_1(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """`link` exits 1 with an informative message on KeyboardInterrupt."""
    monkeypatch.setenv(
        "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "ledger.db")
    )
    monkeypatch.setenv("PLAID_CLIENT_ID", "id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    class _InterruptEvent:
        """Raises KeyboardInterrupt when wait() is called."""

        def wait(self) -> None:
            """Simulate Ctrl-C from the operator."""
            raise KeyboardInterrupt

    class _InterruptAdapter:
        def create_link_token(
            self,
            _user_client_id: str,
            _products: list[str],
            _country_codes: list[str],
            **_kwargs: object,
        ) -> str:
            return "link-sandbox-fake"

    monkeypatch.setattr(
        "claw_plaid_ledger.cli.PlaidClientAdapter.from_config",
        lambda _cfg: _InterruptAdapter(),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.start_link_server",
        lambda _tok: (_FakeServer(), _InterruptEvent(), []),
    )
    monkeypatch.setattr(
        "claw_plaid_ledger.cli.webbrowser.open", lambda _url: None
    )

    exit_code, output = run_main(["link"])

    assert exit_code == 1
    assert "interrupted" in output
