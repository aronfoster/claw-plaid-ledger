"""Structured logging utilities: correlation IDs and secret redaction."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from claw_plaid_ledger.db import LedgerErrorRow, insert_ledger_error

if TYPE_CHECKING:
    from pathlib import Path

# Context variable for the current correlation ID.
# Default to "-" so log lines outside any request/sync context are readable.
_correlation_id_var: ContextVar[str] = ContextVar(
    "correlation_id", default="-"
)

# Keys that must never appear in logged webhook payloads.
_SENSITIVE_KEYS = frozenset({"secret", "token", "password"})


def get_correlation_id() -> str:
    """Return the current correlation ID, or '-' if none is active."""
    return _correlation_id_var.get()


def set_correlation_id(value: str) -> Token[str]:
    """
    Set the correlation ID for the current context.

    Returns the :class:`~contextvars.Token` from ``ContextVar.set()``,
    which callers should pass to :func:`reset_correlation_id` when the
    context ends.
    """
    return _correlation_id_var.set(value)


def reset_correlation_id(token: Token[str]) -> None:
    """Reset the correlation ID to the value it had before the matching set."""
    _correlation_id_var.reset(token)


class CorrelationIdFilter(logging.Filter):
    """
    Inject ``correlation_id`` into every log record.

    Install on the root handler (or any handler that should carry the ID)
    so all loggers pick it up without per-call changes.  The value is read
    from ``_correlation_id_var``; when no request or sync context is active
    it renders as ``"-"``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add correlation_id attribute from context var; never suppresses."""
        # LogRecord is intentionally extensible: adding extra attributes is the
        # documented pattern for custom log formatting.  Direct attribute
        # assignment on the instance is the idiomatic way; ruff B010 does not
        # apply here because the attribute name is dynamic per-record and the
        # assignment is the intended side-effect of the filter.
        record.__dict__["correlation_id"] = _correlation_id_var.get()
        return True


class LedgerDbHandler(logging.Handler):
    """
    Logging handler that persists WARNING+ records to ``ledger_errors``.

    Opens a new SQLite connection per ``emit()`` call (same pattern the
    server uses per request — cheap and thread-safe for SQLite).  A
    ``threading.local()`` guard prevents infinite recursion if the DB layer
    itself ever logs at WARNING+.
    """

    def __init__(self, db_path: Path) -> None:
        """Store the DB path and initialise the re-entrancy guard."""
        super().__init__(level=logging.WARNING)
        self._db_path = db_path
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        """Write one WARNING+ record to ledger_errors; skip on re-entry."""
        if getattr(self._local, "active", False):
            return
        self._local.active = True
        try:
            correlation_id_raw = get_correlation_id()
            correlation_id = (
                correlation_id_raw if correlation_id_raw != "-" else None
            )
            row = LedgerErrorRow(
                severity=record.levelname,
                logger_name=record.name,
                message=self.format(record),
                correlation_id=correlation_id,
                created_at=datetime.fromtimestamp(record.created, tz=UTC),
            )
            with sqlite3.connect(self._db_path) as conn:
                insert_ledger_error(conn, row)
        except Exception:  # noqa: BLE001
            self.handleError(record)
        finally:
            self._local.active = False


def redact_webhook_body(body: dict[str, object]) -> dict[str, object]:
    """
    Return a copy of *body* with sensitive fields removed.

    Removes any top-level key whose name is ``secret``, ``token``, or
    ``password`` (case-sensitive).  Financial data (amounts, account IDs,
    dates) and other Plaid fields are preserved unchanged.

    Example::

        >>> redact_webhook_body({"webhook_type": "SYNC", "token": "abc"})
        {'webhook_type': 'SYNC'}
    """
    return {k: v for k, v in body.items() if k not in _SENSITIVE_KEYS}
