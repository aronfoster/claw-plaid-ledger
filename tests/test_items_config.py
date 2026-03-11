"""items.toml loading tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.items_config import (
    ItemConfig,
    ItemsConfigError,
    SuppressedAccountConfig,
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


# ---------------------------------------------------------------------------
# suppressed_accounts tests
# ---------------------------------------------------------------------------


def test_load_items_config_no_suppressed_accounts_backward_compat(
    tmp_path: Path,
) -> None:
    """Items without suppressed_accounts should parse without change."""
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

    parsed = load_items_config(items_path)

    assert len(parsed) == 1
    assert parsed[0].suppressed_accounts == ()


def test_load_items_config_single_suppressed_account(tmp_path: Path) -> None:
    """A single suppressed_accounts entry should be parsed correctly."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "PLAID_ACCESS_TOKEN_BANK_ALICE"\n'
            'owner = "alice"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            '  plaid_account_id = "plaid_acct_YYYY"\n'
            '  canonical_account_id = "plaid_acct_XXXX"\n'
            '  canonical_from_item = "card-bob"\n'
            '  note = "shared card"\n'
        ),
        encoding="utf-8",
    )

    parsed = load_items_config(items_path)

    assert len(parsed) == 1
    assert parsed[0].suppressed_accounts == (
        SuppressedAccountConfig(
            plaid_account_id="plaid_acct_YYYY",
            canonical_account_id="plaid_acct_XXXX",
            canonical_from_item="card-bob",
            note="shared card",
        ),
    )


def test_load_items_config_multiple_suppressed_accounts(
    tmp_path: Path,
) -> None:
    """Multiple suppressed_accounts entries should all be returned in order."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "TOKEN"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            '  plaid_account_id = "acct_A"\n'
            '  canonical_account_id = "acct_X"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            '  plaid_account_id = "acct_B"\n'
            '  canonical_account_id = "acct_Y"\n'
        ),
        encoding="utf-8",
    )

    parsed = load_items_config(items_path)

    assert parsed[0].suppressed_accounts == (
        SuppressedAccountConfig(
            plaid_account_id="acct_A", canonical_account_id="acct_X"
        ),
        SuppressedAccountConfig(
            plaid_account_id="acct_B", canonical_account_id="acct_Y"
        ),
    )


def test_load_items_config_suppressed_account_optional_fields_default_none(
    tmp_path: Path,
) -> None:
    """canonical_from_item and note should default to None when omitted."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "TOKEN"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            '  plaid_account_id = "acct_A"\n'
            '  canonical_account_id = "acct_X"\n'
        ),
        encoding="utf-8",
    )

    parsed = load_items_config(items_path)

    sa = parsed[0].suppressed_accounts[0]
    assert sa.canonical_from_item is None
    assert sa.note is None


def test_load_items_config_suppressed_account_missing_plaid_account_id_raises(
    tmp_path: Path,
) -> None:
    """Missing plaid_account_id in suppressed_accounts raises error."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "TOKEN"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            '  canonical_account_id = "acct_X"\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ItemsConfigError,
        match=(
            r"items\[0\]\.suppressed_accounts\[0\]"
            r" missing required field 'plaid_account_id'"
        ),
    ):
        load_items_config(items_path)


def test_load_items_config_suppressed_account_missing_canonical_id_raises(
    tmp_path: Path,
) -> None:
    """Missing canonical_account_id in suppressed_accounts raises error."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "TOKEN"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            '  plaid_account_id = "acct_A"\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ItemsConfigError,
        match=(
            r"items\[0\]\.suppressed_accounts\[0\]"
            r" missing required field 'canonical_account_id'"
        ),
    ):
        load_items_config(items_path)


def test_load_items_config_suppressed_account_wrong_type_raises(
    tmp_path: Path,
) -> None:
    """A non-string plaid_account_id should raise ItemsConfigError."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "TOKEN"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            "  plaid_account_id = 999\n"
            '  canonical_account_id = "acct_X"\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ItemsConfigError,
        match=(
            r"items\[0\]\.suppressed_accounts\[0\]"
            r" field 'plaid_account_id' must be a string"
        ),
    ):
        load_items_config(items_path)


def test_load_items_config_suppressed_account_wrong_type_canonical_raises(
    tmp_path: Path,
) -> None:
    """A non-string canonical_account_id should raise ItemsConfigError."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        (
            "[[items]]\n"
            'id = "bank-alice"\n'
            'access_token_env = "TOKEN"\n'
            "\n"
            "  [[items.suppressed_accounts]]\n"
            '  plaid_account_id = "acct_A"\n'
            "  canonical_account_id = 42\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ItemsConfigError,
        match=(
            r"items\[0\]\.suppressed_accounts\[0\]"
            r" field 'canonical_account_id' must be a string"
        ),
    ):
        load_items_config(items_path)
