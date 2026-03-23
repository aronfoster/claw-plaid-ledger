"""Shared utilities for routers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from collections.abc import Callable


_SpendRange = Literal[
    "last_month", "this_month", "last_30_days", "last_7_days"
]


def _today() -> date:
    """Return the current local date. Extracted for testability."""
    return datetime.now(tz=UTC).astimezone().date()


def _resolve_spend_dates(
    date_range: _SpendRange | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    """
    Resolve ``start_date`` and ``end_date`` from a range shorthand.

    If *date_range* is supplied, derive both dates from it using server local
    time, then apply any explicit ``start_date``/``end_date`` overrides.
    If *date_range* is absent, both ``start_date`` and ``end_date`` must be
    present; otherwise raises HTTP 422.
    """
    if date_range is not None:
        today = _today()
        if date_range == "this_month":
            derived_start: date = today.replace(day=1)
            derived_end: date = today
        elif date_range == "last_month":
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            derived_start = last_month_end.replace(day=1)
            derived_end = last_month_end
        elif date_range == "last_30_days":
            derived_start = today - timedelta(days=30)
            derived_end = today
        else:  # last_7_days
            derived_start = today - timedelta(days=7)
            derived_end = today
        resolved_start = (
            start_date if start_date is not None else derived_start
        )
        resolved_end = end_date if end_date is not None else derived_end
        return resolved_start, resolved_end

    if start_date is None or end_date is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Provide either 'range' or both 'start_date' and 'end_date'."
            ),
        )
    return start_date, end_date


def _strict_params(allowed: frozenset[str]) -> Callable[[Request], None]:
    """Raise 422 if the request contains any query parameter not in allowed."""

    def _check(request: Request) -> None:
        unknown = sorted(set(request.query_params.keys()) - allowed)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unrecognized query parameters",
                    "unrecognized": unknown,
                    "valid_parameters": sorted(allowed),
                },
            )

    return _check
