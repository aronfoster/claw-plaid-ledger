"""Tests for background sync, lifespan, and scheduled-sync helpers."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claw_plaid_ledger.config import Config, OpenClawConfig
from claw_plaid_ledger.db import initialize_database, upsert_sync_state
from claw_plaid_ledger.items_config import ItemConfig
from claw_plaid_ledger.routers.webhooks import (
    _background_sync,
    _check_and_sync_overdue_items,
    _scheduled_sync_loop,
    lifespan,
)
from claw_plaid_ledger.server import app
from claw_plaid_ledger.sync_engine import SyncSummary

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Generator

_OC_TOKEN = "test-oc-token"  # noqa: S105
_OC_URL = "http://127.0.0.1:18789/hooks/agent"

_FALLBACK_HOURS_24 = 24
_EXPECTED_TWO_CALLS = 2
_EXPECTED_ONE_CALL = 1


def _make_mock_config(tmp_path: pathlib.Path) -> MagicMock:
    """Return a minimal mock Config for _background_sync tests."""
    mock_config = MagicMock()
    mock_config.plaid_access_token = "access-token"  # noqa: S105
    mock_config.item_id = "default-item"
    mock_config.db_path = tmp_path
    mock_config.openclaw_hooks_url = _OC_URL
    mock_config.openclaw_hooks_token = _OC_TOKEN
    mock_config.openclaw_hooks_agent = "Hestia"
    mock_config.openclaw_hooks_wake_mode = "now"
    return mock_config


def _make_scheduled_sync_config(
    tmp_path: pathlib.Path,
    *,
    fallback_hours: int = 24,
) -> Config:
    """Build a minimal Config for scheduled-sync tests."""
    return Config(
        db_path=tmp_path / "db.sqlite",
        workspace_path=None,
        plaid_client_id=None,
        plaid_secret=None,
        plaid_env=None,
        plaid_access_token=None,
        scheduled_sync_enabled=True,
        scheduled_sync_fallback_hours=fallback_hours,
    )


# ---------------------------------------------------------------------------
# Tests for notify_openclaw wiring in _background_sync
# ---------------------------------------------------------------------------


class TestBackgroundSyncNotificationWiring:
    """Tests that _background_sync wires notify_openclaw correctly."""

    def test_notify_called_when_sync_has_changes(
        self, tmp_path: pathlib.Path
    ) -> None:
        """notify_openclaw called once when sync has non-zero changes."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=3, modified=1, removed=0, accounts=1, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"),
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                return_value=summary,
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.notify_openclaw"
            ) as mock_notify,
        ):
            asyncio.run(_background_sync())

        expected_openclaw_cfg = OpenClawConfig(
            url=mock_config.openclaw_hooks_url,
            token=mock_config.openclaw_hooks_token,
            agent=mock_config.openclaw_hooks_agent,
            wake_mode=mock_config.openclaw_hooks_wake_mode,
        )
        mock_notify.assert_called_once_with(summary, expected_openclaw_cfg)

    def test_notify_not_called_when_sync_has_no_changes(
        self, tmp_path: pathlib.Path
    ) -> None:
        """notify_openclaw not called when sync returns zero changes."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=0, modified=0, removed=0, accounts=0, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"),
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                return_value=summary,
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.notify_openclaw"
            ) as mock_notify,
        ):
            asyncio.run(_background_sync())

        mock_notify.assert_not_called()

    def test_notify_exception_does_not_propagate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Exception from notify_openclaw is caught; sync does not crash."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=1, modified=0, removed=0, accounts=1, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"),
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                return_value=summary,
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks.notify_openclaw",
                side_effect=RuntimeError("notifier bug"),
            ),
        ):
            # Must not raise — except Exception in _background_sync absorbs it.
            asyncio.run(_background_sync())


# ---------------------------------------------------------------------------
# Tests for _background_sync with injected credentials
# ---------------------------------------------------------------------------


class TestBackgroundSyncInjectedCredentials:
    """Tests for _background_sync() with explicit access_token / item_id."""

    def test_injected_access_token_is_passed_to_run_sync(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Injected access_token and item_id are forwarded to run_sync."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=0, modified=0, removed=0, accounts=0, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"),
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                return_value=summary,
            ) as mock_run_sync,
        ):
            asyncio.run(
                _background_sync(
                    # S106: test fixture value, not a real credential
                    access_token="injected-token",  # noqa: S106
                    item_id="bank-alice",
                    owner="alice",
                )
            )

        mock_run_sync.assert_called_once()
        call_kwargs = mock_run_sync.call_args.kwargs
        # S105: comparing against a test fixture token, not a real credential
        assert call_kwargs["access_token"] == "injected-token"  # noqa: S105
        assert call_kwargs["item_id"] == "bank-alice"
        assert call_kwargs["owner"] == "alice"

    def test_no_args_uses_config_values_backward_compat(
        self, tmp_path: pathlib.Path
    ) -> None:
        """_background_sync() with no args uses config token and item_id."""
        mock_config = _make_mock_config(tmp_path)
        summary = SyncSummary(
            added=0, modified=0, removed=0, accounts=0, next_cursor="cur"
        )

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_config",
                return_value=mock_config,
            ),
            patch("claw_plaid_ledger.routers.webhooks.PlaidClientAdapter"),
            patch(
                "claw_plaid_ledger.routers.webhooks.run_sync",
                return_value=summary,
            ) as mock_run_sync,
        ):
            asyncio.run(_background_sync())

        mock_run_sync.assert_called_once()
        call_kwargs = mock_run_sync.call_args.kwargs
        assert call_kwargs["access_token"] == mock_config.plaid_access_token
        assert call_kwargs["item_id"] == mock_config.item_id


# ---------------------------------------------------------------------------
# Tests for lifespan context manager
# ---------------------------------------------------------------------------


class TestLifespan:
    """Tests for the FastAPI lifespan startup/shutdown behavior."""

    def test_disabled_no_task_created(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """No background task is created when scheduled sync is disabled."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_SCHEDULED_SYNC_ENABLED", "false")

        created_tasks: list[object] = []

        def _capture_task(coro: object) -> MagicMock:
            created_tasks.append(coro)
            return MagicMock()

        async def _run() -> None:
            with patch(
                "claw_plaid_ledger.routers.webhooks.asyncio.create_task",
                side_effect=_capture_task,
            ):
                async with lifespan(app):
                    pass

        asyncio.run(_run())
        assert created_tasks == []

    def test_enabled_task_created_and_cancelled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Enabled scheduled sync starts a task cancelled on shutdown."""
        monkeypatch.setenv(
            "CLAW_PLAID_LEDGER_DB_PATH", str(tmp_path / "db.sqlite")
        )
        monkeypatch.setenv("CLAW_SCHEDULED_SYNC_ENABLED", "true")

        cancel_called: list[bool] = []

        class _FakeTask:
            """Minimal awaitable that raises CancelledError on await."""

            def cancel(self) -> None:
                cancel_called.append(True)

            def __await__(
                self,
            ) -> Generator[Any, None, None]:
                raise asyncio.CancelledError
                yield  # pragma: no cover  # makes this a generator function

        task_fake = _FakeTask()

        async def _run() -> None:
            with patch(
                "claw_plaid_ledger.routers.webhooks.asyncio.create_task",
                return_value=task_fake,
            ) as mock_create:
                async with lifespan(app):
                    pass

            mock_create.assert_called_once()
            assert cancel_called == [True]

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests for _check_and_sync_overdue_items
# ---------------------------------------------------------------------------


class TestCheckAndSyncOverdueItems:
    """Tests for _check_and_sync_overdue_items()."""

    def test_overdue_item_triggers_sync(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Item last synced more than fallback_hours ago triggers sync."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        old_ts = (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat()
        with sqlite3.connect(db_path) as conn:
            upsert_sync_state(
                conn, item_id="bank-alice", cursor=None, last_synced_at=old_ts
            )

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_ALICE", "tok-alice")
        # S106: access_token_env holds an env-var name, not a token literal.
        item_cfg = ItemConfig(
            id="bank-alice",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_ALICE",  # noqa: S106
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[item_cfg],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
            ),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        mock_bg.assert_called_once()
        call_kwargs = mock_bg.call_args.kwargs
        assert call_kwargs["item_id"] == "bank-alice"

    def test_recent_item_skipped(self, tmp_path: pathlib.Path) -> None:
        """An item synced within the fallback window is not re-synced."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)
        recent_ts = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
        with sqlite3.connect(db_path) as conn:
            upsert_sync_state(
                conn,
                item_id="bank-alice",
                cursor=None,
                last_synced_at=recent_ts,
            )

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        # S106: access_token_env holds an env-var name, not a token literal.
        item_cfg = ItemConfig(
            id="bank-alice",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_ALICE",  # noqa: S106
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[item_cfg],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
            ),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        mock_bg.assert_not_called()

    def test_item_with_no_sync_state_treated_as_overdue(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """An item with no entry in sync_state is treated as overdue."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_BOB", "tok-bob")
        # S106: access_token_env holds an env-var name, not a token literal.
        item_cfg = ItemConfig(
            id="bank-bob",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_BOB",  # noqa: S106
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[item_cfg],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
            ),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        mock_bg.assert_called_once()

    def test_one_item_failure_does_not_prevent_others(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Exception for one item is caught; others are still checked."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_ALICE", "tok-alice")
        monkeypatch.setenv("PLAID_ACCESS_TOKEN_BANK_BOB", "tok-bob")
        # S106: access_token_env holds an env-var name, not a token literal.
        item_alice = ItemConfig(
            id="bank-alice",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_ALICE",  # noqa: S106
        )
        item_bob = ItemConfig(
            id="bank-bob",
            access_token_env="PLAID_ACCESS_TOKEN_BANK_BOB",  # noqa: S106
        )

        call_count = 0
        _err_msg = "alice sync failed"

        async def _flaky_bg(**kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if kwargs.get("item_id") == "bank-alice":
                raise RuntimeError(_err_msg)

        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[item_alice, item_bob],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._background_sync",
                side_effect=_flaky_bg,
            ),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        # bank-bob must have been reached even though bank-alice raised
        assert call_count == _EXPECTED_TWO_CALLS

    def test_no_items_toml_uses_single_item_fallback(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When items.toml is absent, the single-item fallback is checked."""
        db_path = tmp_path / "db.sqlite"
        initialize_database(db_path)

        cfg = _make_scheduled_sync_config(
            tmp_path, fallback_hours=_FALLBACK_HOURS_24
        )

        mock_bg = AsyncMock()
        with (
            patch(
                "claw_plaid_ledger.routers.webhooks.load_items_config",
                return_value=[],
            ),
            patch(
                "claw_plaid_ledger.routers.webhooks._background_sync", mock_bg
            ),
        ):
            asyncio.run(_check_and_sync_overdue_items(cfg))

        # _background_sync called with no arguments (single-item path)
        mock_bg.assert_called_once_with()


# ---------------------------------------------------------------------------
# Tests for _scheduled_sync_loop
# ---------------------------------------------------------------------------


class TestScheduledSyncLoop:
    """Tests for _scheduled_sync_loop()."""

    def test_loop_calls_check_after_sleep(
        self, tmp_path: pathlib.Path
    ) -> None:
        """_scheduled_sync_loop calls _check_and_sync_overdue_items."""
        cfg = _make_scheduled_sync_config(tmp_path)

        check_calls: list[object] = []

        async def _fake_check(config: Config) -> None:
            check_calls.append(config)
            # Stop the loop after the first check by raising CancelledError.
            raise asyncio.CancelledError

        async def _run() -> None:
            with (
                patch(
                    "claw_plaid_ledger.routers.webhooks.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "claw_plaid_ledger.routers.webhooks._check_and_sync_overdue_items",
                    side_effect=_fake_check,
                ),
                pytest.raises(asyncio.CancelledError),
            ):
                await _scheduled_sync_loop(cfg)

        asyncio.run(_run())
        assert len(check_calls) == _EXPECTED_ONE_CALL
        assert check_calls[0] is cfg
