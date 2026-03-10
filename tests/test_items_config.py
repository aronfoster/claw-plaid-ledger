"""items.toml loading tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.items_config import (
    ItemConfig,
    ItemsConfigError,
    load_items_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_load_items_config_returns_empty_list_when_file_not_found(
    tmp_path: Path,
) -> None:
    """Missing items.toml should return an empty list."""
    missing_path = tmp_path / "missing.toml"

    assert load_items_config(missing_path) == []


def test_load_items_config_returns_empty_list_for_no_items_key(
    tmp_path: Path,
) -> None:
    """An items.toml without an items key should return an empty list."""
    items_path = tmp_path / "items.toml"
    items_path.write_text('title = "household"\n', encoding="utf-8")

    assert load_items_config(items_path) == []


def test_load_items_config_single_item_all_fields_present(
    tmp_path: Path,
) -> None:
    """A one-item config should parse into one ItemConfig instance."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"\n'
            'owner = "alice"\n'
        ),
        encoding="utf-8",
    )

    assert load_items_config(items_path) == [
        ItemConfig("bank-alice", "PLAID_ACCESS_TOKEN_BANK_ALICE", "alice")
    ]


def test_load_items_config_multiple_items_preserve_order(
    tmp_path: Path,
) -> None:
    """Multiple items should be returned in file order."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "first"\n'
            'access_token_env = "TOKEN_FIRST"\n'
            "\n"
            "[[items]]\n"
            'id = "second"\n'
            'access_token_env = "TOKEN_SECOND"\n'
            'owner = "shared"\n'
        ),
        encoding="utf-8",
    )

    parsed = load_items_config(items_path)

    assert parsed == [
        ItemConfig("first", "TOKEN_FIRST", None),
        ItemConfig("second", "TOKEN_SECOND", "shared"),
    ]


def test_load_items_config_owner_absent_defaults_to_none(
    tmp_path: Path,
) -> None:
    """Owner should default to None when omitted."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        ('[[items]]\nid = "bank"\naccess_token_env = "TOKEN"\n'),
        encoding="utf-8",
    )

    parsed = load_items_config(items_path)

    assert parsed[0].owner is None


def test_load_items_config_missing_id_raises_with_index(
    tmp_path: Path,
) -> None:
    """Missing id should raise ItemsConfigError naming the item index."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        ('[[items]]\naccess_token_env = "TOKEN"\n'),
        encoding="utf-8",
    )

    with pytest.raises(
        ItemsConfigError, match=r"items\[0\] missing required field 'id'"
    ):
        load_items_config(items_path)


def test_load_items_config_missing_access_token_env_raises(
    tmp_path: Path,
) -> None:
    """Missing access_token_env should raise ItemsConfigError."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        ('[[items]]\nid = "bank"\n'),
        encoding="utf-8",
    )

    with pytest.raises(
        ItemsConfigError,
        match=r"items\[0\] missing required field 'access_token_env'",
    ):
        load_items_config(items_path)


def test_load_items_config_id_must_be_string(tmp_path: Path) -> None:
    """Id field values must be strings."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        ('[[items]]\nid = 123\naccess_token_env = "TOKEN"\n'),
        encoding="utf-8",
    )

    with pytest.raises(
        ItemsConfigError,
        match=r"items\[0\] field 'id' must be a string",
    ):
        load_items_config(items_path)
