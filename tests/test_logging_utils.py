"""Tests for claw_plaid_ledger.logging_utils."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

from claw_plaid_ledger.db import (
    LedgerErrorQuery,
    initialize_database,
    query_ledger_errors,
)
from claw_plaid_ledger.logging_utils import (
    CorrelationIdFilter,
    LedgerDbHandler,
    _correlation_id_var,
    get_correlation_id,
    redact_webhook_body,
    reset_correlation_id,
    set_correlation_id,
)

if TYPE_CHECKING:
    import pathlib

    import pytest


# ---------------------------------------------------------------------------
# ContextVar helpers
# ---------------------------------------------------------------------------


class TestCorrelationIdVar:
    """Tests for set/get/reset correlation ID helpers."""

    def test_default_returns_dash(self) -> None:
        """Without any context set, get_correlation_id returns '-'."""
        token = _correlation_id_var.set("-")
        try:
            assert get_correlation_id() == "-"
        finally:
            _correlation_id_var.reset(token)

    def test_set_and_get(self) -> None:
        """set_correlation_id makes value readable via get_correlation_id."""
        token = set_correlation_id("req-abc12345")
        try:
            assert get_correlation_id() == "req-abc12345"
        finally:
            reset_correlation_id(token)

    def test_reset_restores_previous_value(self) -> None:
        """reset_correlation_id restores the prior value."""
        outer_token = set_correlation_id("outer")
        try:
            inner_token = set_correlation_id("inner")
            assert get_correlation_id() == "inner"
            reset_correlation_id(inner_token)
            assert get_correlation_id() == "outer"
        finally:
            reset_correlation_id(outer_token)


# ---------------------------------------------------------------------------
# CorrelationIdFilter
# ---------------------------------------------------------------------------


class TestCorrelationIdFilter:
    """Tests for the CorrelationIdFilter logging.Filter."""

    def test_filter_adds_correlation_id_to_record(self) -> None:
        """filter() sets correlation_id on the log record."""
        token = set_correlation_id("req-test0001")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            filt = CorrelationIdFilter()
            result = filt.filter(record)
            assert result is True
            assert record.__dict__["correlation_id"] == "req-test0001"
        finally:
            reset_correlation_id(token)

    def test_filter_uses_dash_when_no_context(self) -> None:
        """When correlation_id is unset, the filter injects '-'."""
        # Reset to default
        token = _correlation_id_var.set("-")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            filt = CorrelationIdFilter()
            filt.filter(record)
            assert record.__dict__["correlation_id"] == "-"
        finally:
            _correlation_id_var.reset(token)

    def test_filter_always_returns_true(self) -> None:
        """filter() returns True (never suppresses records)."""
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="msg",
            args=(),
            exc_info=None,
        )
        filt = CorrelationIdFilter()
        assert filt.filter(record) is True


# ---------------------------------------------------------------------------
# redact_webhook_body
# ---------------------------------------------------------------------------


class TestRedactWebhookBody:
    """Tests for redact_webhook_body."""

    def test_removes_secret_key(self) -> None:
        """A top-level 'secret' key is removed."""
        body: dict[str, object] = {"webhook_type": "SYNC", "secret": "s3cr3t"}
        result = redact_webhook_body(body)
        assert "secret" not in result
        assert result["webhook_type"] == "SYNC"

    def test_removes_token_key(self) -> None:
        """A top-level 'token' key is removed."""
        body: dict[str, object] = {"item_id": "bank-alice", "token": "tok"}
        result = redact_webhook_body(body)
        assert "token" not in result
        assert result["item_id"] == "bank-alice"

    def test_removes_password_key(self) -> None:
        """A top-level 'password' key is removed."""
        body: dict[str, object] = {"name": "alice", "password": "hunter2"}
        result = redact_webhook_body(body)
        assert "password" not in result
        assert result["name"] == "alice"

    def test_removes_all_sensitive_keys_at_once(self) -> None:
        """All three sensitive keys are removed in a single call."""
        body: dict[str, object] = {
            "webhook_type": "SYNC",
            "secret": "s3c",
            "token": "tok",
            "password": "pw",
        }
        result = redact_webhook_body(body)
        assert "secret" not in result
        assert "token" not in result
        assert "password" not in result
        assert result["webhook_type"] == "SYNC"

    def test_preserves_financial_data(self) -> None:
        """Amount, account_id, and date fields are not removed."""
        body: dict[str, object] = {
            "item_id": "bank-alice",
            "amount": 42.50,
            "account_id": "acct_xyz",
            "posted_date": "2025-01-15",
        }
        result = redact_webhook_body(body)
        assert result == body

    def test_returns_copy_not_mutating_original(self) -> None:
        """redact_webhook_body does not modify the original dict."""
        body: dict[str, object] = {"webhook_type": "SYNC", "token": "tok"}
        original = dict(body)
        redact_webhook_body(body)
        assert body == original

    def test_empty_body_returns_empty_dict(self) -> None:
        """An empty dict passes through unchanged."""
        assert redact_webhook_body({}) == {}

    def test_no_sensitive_keys_returns_unchanged(self) -> None:
        """A body with no sensitive keys is returned as-is."""
        body: dict[str, object] = {
            "webhook_type": "ITEM_WEBHOOK",
            "item_id": "x",
        }
        assert redact_webhook_body(body) == body


# ---------------------------------------------------------------------------
# Log format integration: correlation_id appears in formatted output
# ---------------------------------------------------------------------------


class TestLogFormat:
    """End-to-end test: filter + basicConfig format renders correlation_id."""

    def test_correlation_id_appears_in_log_output(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CorrelationIdFilter injects the ID so caplog records carry it."""
        token = set_correlation_id("req-cafebabe")
        try:
            filt = CorrelationIdFilter()
            test_logger = logging.getLogger("test_format_logger")
            with caplog.at_level(logging.INFO, logger="test_format_logger"):
                # Add filter to root so caplog picks it up
                caplog.handler.addFilter(filt)
                test_logger.info("hello world")
                caplog.handler.removeFilter(filt)

            assert caplog.records
            assert (
                caplog.records[0].__dict__["correlation_id"] == "req-cafebabe"
            )
        finally:
            reset_correlation_id(token)


# ---------------------------------------------------------------------------
# LedgerDbHandler
# ---------------------------------------------------------------------------


def _make_log_record(
    level: int, msg: str, logger_name: str = "test.handler"
) -> logging.LogRecord:
    """Create a minimal LogRecord for testing."""
    record = logging.LogRecord(
        name=logger_name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    record.created = datetime.now(UTC).timestamp()
    return record


class TestLedgerDbHandler:
    """Tests for LedgerDbHandler."""

    def test_emit_warning_writes_row(self, tmp_path: pathlib.Path) -> None:
        """Emitting a WARNING record persists one row in ledger_errors."""
        db_path = tmp_path / "test.db"
        initialize_database(db_path)
        handler = LedgerDbHandler(db_path)

        record = _make_log_record(logging.WARNING, "test warning message")
        handler.emit(record)

        with sqlite3.connect(db_path) as conn:
            rows, total = query_ledger_errors(conn, LedgerErrorQuery())
        assert total == 1
        assert rows[0]["severity"] == "WARNING"
        assert rows[0]["message"] == "test warning message"

    def test_emit_error_writes_row(self, tmp_path: pathlib.Path) -> None:
        """Emitting an ERROR record persists one row in ledger_errors."""
        db_path = tmp_path / "test.db"
        initialize_database(db_path)
        handler = LedgerDbHandler(db_path)

        record = _make_log_record(logging.ERROR, "test error message")
        handler.emit(record)

        with sqlite3.connect(db_path) as conn:
            rows, total = query_ledger_errors(conn, LedgerErrorQuery())
        assert total == 1
        assert rows[0]["severity"] == "ERROR"

    def test_emit_info_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """INFO records are not written; handler level is WARNING."""
        db_path = tmp_path / "test.db"
        initialize_database(db_path)
        handler = LedgerDbHandler(db_path)

        # Attach the handler to a temporary logger and send an INFO record
        # through the logger — the logging framework checks handler.level
        # before calling emit(), so INFO is never written to the DB.
        test_logger = logging.getLogger("test_emit_info_ignored")
        test_logger.setLevel(logging.DEBUG)
        test_logger.addHandler(handler)
        try:
            test_logger.info("should not be stored")
        finally:
            test_logger.removeHandler(handler)

        with sqlite3.connect(db_path) as conn:
            _, total = query_ledger_errors(conn, LedgerErrorQuery())
        assert total == 0

    def test_emit_carries_correlation_id(self, tmp_path: pathlib.Path) -> None:
        """Correlation ID set via set_correlation_id() appears in the row."""
        db_path = tmp_path / "test.db"
        initialize_database(db_path)
        handler = LedgerDbHandler(db_path)

        token = set_correlation_id("req-test1234")
        try:
            record = _make_log_record(logging.WARNING, "correlated warning")
            handler.emit(record)
        finally:
            reset_correlation_id(token)

        with sqlite3.connect(db_path) as conn:
            rows, _ = query_ledger_errors(conn, LedgerErrorQuery())
        assert rows[0]["correlation_id"] == "req-test1234"

    def test_reentrancy_guard_prevents_recursion(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Re-entrant emit() calls are dropped; no exception raised."""
        db_path = tmp_path / "test.db"
        initialize_database(db_path)
        handler = LedgerDbHandler(db_path)

        call_count = 0

        def re_entrant_insert(_conn: object, _row: object) -> None:
            nonlocal call_count
            call_count += 1
            # Try to emit again on the same handler — should be a no-op.
            inner_record = _make_log_record(logging.ERROR, "inner")
            handler.emit(inner_record)

        with patch(
            "claw_plaid_ledger.logging_utils.insert_ledger_error",
            side_effect=re_entrant_insert,
        ):
            record = _make_log_record(logging.WARNING, "outer")
            handler.emit(record)

        # The inner re-entrant call was blocked; insert was called only once.
        assert call_count == 1
