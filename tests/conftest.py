"""Shared test fixtures for claw-plaid-ledger."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

_NONEXISTENT_ENV_FILE = Path("/nonexistent/claw-plaid-ledger-test.env")


@pytest.fixture(autouse=True)
def _isolate_env_file() -> Iterator[None]:
    """Prevent tests from reading the real ~/.config/claw-plaid-ledger/.env."""
    with (
        patch(
            "claw_plaid_ledger.config._default_env_file",
            return_value=_NONEXISTENT_ENV_FILE,
        ),
        patch(
            "claw_plaid_ledger.preflight._default_env_file",
            return_value=_NONEXISTENT_ENV_FILE,
        ),
    ):
        yield
