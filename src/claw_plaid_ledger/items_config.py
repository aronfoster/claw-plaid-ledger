"""Multi-item Plaid configuration loader (items.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ITEMS_TOML = Path(
    "~/.config/claw-plaid-ledger/items.toml"
).expanduser()


class ItemsConfigError(ValueError):
    """Raised when items.toml is malformed or missing a required field."""

    @classmethod
    def for_missing_field(cls, *, index: int, field: str) -> ItemsConfigError:
        """Build an error for a missing required field in one item."""
        message = f"items[{index}] missing required field '{field}'"
        return cls(message)

    @classmethod
    def for_invalid_type(
        cls,
        *,
        index: int,
        field: str,
        expected: str,
    ) -> ItemsConfigError:
        """Build an error for an item field with the wrong type."""
        message = f"items[{index}] field '{field}' must be {expected}"
        return cls(message)

    @classmethod
    def for_invalid_top_level_items(cls) -> ItemsConfigError:
        """Build an error for an invalid top-level items value."""
        message = "top-level 'items' must be a list"
        return cls(message)

    @classmethod
    def for_invalid_item_entry(cls, *, index: int) -> ItemsConfigError:
        """Build an error for a non-table item entry."""
        message = f"items[{index}] must be a table"
        return cls(message)

    @classmethod
    def for_invalid_suppressed_account_entry(
        cls, *, item_index: int, sa_index: int
    ) -> ItemsConfigError:
        """Build an error for a non-table suppressed_accounts entry."""
        message = (
            f"items[{item_index}].suppressed_accounts[{sa_index}]"
            " must be a table"
        )
        return cls(message)

    @classmethod
    def for_missing_suppressed_account_field(
        cls, *, item_index: int, sa_index: int, field: str
    ) -> ItemsConfigError:
        """Build an error for a missing required suppressed-entry field."""
        message = (
            f"items[{item_index}].suppressed_accounts[{sa_index}]"
            f" missing required field '{field}'"
        )
        return cls(message)

    @classmethod
    def for_invalid_suppressed_account_type(
        cls, *, item_index: int, sa_index: int, field: str, expected: str
    ) -> ItemsConfigError:
        """Build an error for a suppressed entry field with the wrong type."""
        message = (
            f"items[{item_index}].suppressed_accounts[{sa_index}]"
            f" field '{field}' must be {expected}"
        )
        return cls(message)


@dataclass(frozen=True)
class SuppressedAccountConfig:
    """One suppressed-account alias declared inside an [[items]] block."""

    plaid_account_id: str
    canonical_account_id: str
    canonical_from_item: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ItemConfig:
    """Configuration for one Plaid item (institution + access token)."""

    id: str
    access_token_env: str
    owner: str | None = None
    suppressed_accounts: tuple[SuppressedAccountConfig, ...] = ()


def _parse_suppressed_account(
    raw: object, *, item_index: int, sa_index: int
) -> SuppressedAccountConfig:
    if not isinstance(raw, dict):
        raise ItemsConfigError.for_invalid_suppressed_account_entry(
            item_index=item_index, sa_index=sa_index
        )

    plaid_account_id = raw.get("plaid_account_id")
    if plaid_account_id is None:
        raise ItemsConfigError.for_missing_suppressed_account_field(
            item_index=item_index, sa_index=sa_index, field="plaid_account_id"
        )
    if not isinstance(plaid_account_id, str):
        raise ItemsConfigError.for_invalid_suppressed_account_type(
            item_index=item_index,
            sa_index=sa_index,
            field="plaid_account_id",
            expected="a string",
        )

    canonical_account_id = raw.get("canonical_account_id")
    if canonical_account_id is None:
        raise ItemsConfigError.for_missing_suppressed_account_field(
            item_index=item_index,
            sa_index=sa_index,
            field="canonical_account_id",
        )
    if not isinstance(canonical_account_id, str):
        raise ItemsConfigError.for_invalid_suppressed_account_type(
            item_index=item_index,
            sa_index=sa_index,
            field="canonical_account_id",
            expected="a string",
        )

    canonical_from_item = raw.get("canonical_from_item")
    if canonical_from_item is not None and not isinstance(
        canonical_from_item, str
    ):
        raise ItemsConfigError.for_invalid_suppressed_account_type(
            item_index=item_index,
            sa_index=sa_index,
            field="canonical_from_item",
            expected="a string",
        )

    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        raise ItemsConfigError.for_invalid_suppressed_account_type(
            item_index=item_index,
            sa_index=sa_index,
            field="note",
            expected="a string",
        )

    return SuppressedAccountConfig(
        plaid_account_id=plaid_account_id,
        canonical_account_id=canonical_account_id,
        canonical_from_item=canonical_from_item,
        note=note,
    )


def _parse_item(raw_item: object, *, index: int) -> ItemConfig:
    if not isinstance(raw_item, dict):
        raise ItemsConfigError.for_invalid_item_entry(index=index)

    item_id = raw_item.get("id")
    if item_id is None:
        raise ItemsConfigError.for_missing_field(index=index, field="id")
    if not isinstance(item_id, str):
        raise ItemsConfigError.for_invalid_type(
            index=index,
            field="id",
            expected="a string",
        )

    access_token_env = raw_item.get("access_token_env")
    if access_token_env is None:
        raise ItemsConfigError.for_missing_field(
            index=index,
            field="access_token_env",
        )
    if not isinstance(access_token_env, str):
        raise ItemsConfigError.for_invalid_type(
            index=index,
            field="access_token_env",
            expected="a string",
        )

    owner_value = raw_item.get("owner")
    if owner_value is not None and not isinstance(owner_value, str):
        raise ItemsConfigError.for_invalid_type(
            index=index,
            field="owner",
            expected="a string",
        )

    raw_suppressed = raw_item.get("suppressed_accounts")
    suppressed_accounts: tuple[SuppressedAccountConfig, ...]
    if raw_suppressed is None:
        suppressed_accounts = ()
    else:
        suppressed_accounts = tuple(
            _parse_suppressed_account(
                entry, item_index=index, sa_index=sa_index
            )
            for sa_index, entry in enumerate(raw_suppressed)
        )

    return ItemConfig(
        id=item_id,
        access_token_env=access_token_env,
        owner=owner_value,
        suppressed_accounts=suppressed_accounts,
    )


def load_items_config(path: Path | None = None) -> list[ItemConfig]:
    """Load and parse items.toml, returning [] when the file is absent."""
    resolved_path = DEFAULT_ITEMS_TOML if path is None else path
    if not resolved_path.exists():
        return []

    with resolved_path.open("rb") as file_handle:
        data = tomllib.load(file_handle)

    raw_items = data.get("items")
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise ItemsConfigError.for_invalid_top_level_items()

    return [
        _parse_item(raw_item, index=index)
        for index, raw_item in enumerate(raw_items)
    ]
