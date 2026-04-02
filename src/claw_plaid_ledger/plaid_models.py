"""
Typed internal models for Plaid data.

These dataclasses represent the narrow slice of Plaid data this application
needs.  No Plaid SDK symbols are imported here; the adapter module is
responsible for translating SDK objects into these types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime


@dataclass(frozen=True)
class AccountData:
    """Typed representation of a Plaid account, for DB upsert."""

    plaid_account_id: str
    name: str
    type: str
    subtype: str | None
    mask: str | None


@dataclass(frozen=True)
class TransactionData:
    """Typed representation of a Plaid transaction, for DB upsert."""

    plaid_transaction_id: str
    plaid_account_id: str
    amount: float
    date: datetime.date
    name: str
    pending: bool
    merchant_name: str | None
    iso_currency_code: str | None


@dataclass(frozen=True)
class RemovedTransactionData:
    """Identifies a transaction that Plaid has removed."""

    plaid_transaction_id: str


@dataclass(frozen=True)
class SyncResult:
    """Typed result of one Plaid transactions/sync call."""

    added: tuple[TransactionData, ...]
    modified: tuple[TransactionData, ...]
    removed: tuple[RemovedTransactionData, ...]
    accounts: tuple[AccountData, ...]
    next_cursor: str
    has_more: bool
    plaid_item_id: str | None = None
