"""
Plaid API adapter.

This is the only module in the codebase that imports Plaid SDK symbols.
All Plaid SDK objects are translated into typed internal models before
they leave this module.  The rest of the application must not import
from plaid directly.
"""

from __future__ import annotations

import datetime
from typing import Any

import plaid  # type: ignore[import-untyped]
from plaid.api import plaid_api  # type: ignore[import-untyped]
from plaid.model.transactions_sync_request import (  # type: ignore[import-untyped]
    TransactionsSyncRequest,
)

from claw_plaid_ledger.config import Config, ConfigError
from claw_plaid_ledger.plaid_models import (
    AccountData,
    RemovedTransactionData,
    SyncResult,
    TransactionData,
)

_ENV_MAP: dict[str, str] = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

SUPPORTED_ENVIRONMENTS: frozenset[str] = frozenset(_ENV_MAP)


def _resolve_host(plaid_env: str) -> str:
    """Map a PLAID_ENV string to a Plaid base URL."""
    try:
        return _ENV_MAP[plaid_env.lower()]
    except KeyError:
        supported = ", ".join(sorted(_ENV_MAP))
        msg = (
            f"Unsupported PLAID_ENV '{plaid_env}'. "
            f"Supported values: {supported}"
        )
        raise ConfigError(msg) from None


def _to_account_data(raw: Any) -> AccountData:
    """Translate a Plaid SDK AccountBase object into an AccountData."""
    subtype: str | None = str(raw.subtype) if raw.subtype is not None else None
    mask: str | None = str(raw.mask) if raw.mask is not None else None
    return AccountData(
        plaid_account_id=str(raw.account_id),
        name=str(raw.name),
        type=str(raw.type),
        subtype=subtype,
        mask=mask,
    )


def _to_transaction_data(raw: Any) -> TransactionData:
    """Translate a Plaid SDK Transaction object into a TransactionData."""
    date: datetime.date = raw.date
    if not isinstance(date, datetime.date):
        date = datetime.date.fromisoformat(str(date))
    merchant: str | None = (
        str(raw.merchant_name) if raw.merchant_name is not None else None
    )
    currency: str | None = (
        str(raw.iso_currency_code)
        if raw.iso_currency_code is not None
        else None
    )
    return TransactionData(
        plaid_transaction_id=str(raw.transaction_id),
        plaid_account_id=str(raw.account_id),
        amount=float(raw.amount),
        date=date,
        name=str(raw.name),
        pending=bool(raw.pending),
        merchant_name=merchant,
        iso_currency_code=currency,
    )


def _to_removed_transaction_data(raw: Any) -> RemovedTransactionData:
    """Translate a Plaid RemovedTransaction into a RemovedTransactionData."""
    return RemovedTransactionData(plaid_transaction_id=str(raw.transaction_id))


class PlaidClientAdapter:
    """
    Typed adapter around the Plaid Python SDK.

    This is the only class that interacts with Plaid SDK objects. Every
    public method accepts and returns typed internal models only; no Plaid
    SDK types escape this boundary.
    """

    def __init__(self, api: Any) -> None:
        """Store the raw Plaid API client."""
        self._api = api

    @classmethod
    def from_config(cls, config: Config) -> PlaidClientAdapter:
        """Construct a PlaidClientAdapter from application configuration."""
        if not config.plaid_client_id:
            raise ConfigError.for_missing_env_vars(["PLAID_CLIENT_ID"])
        if not config.plaid_secret:
            raise ConfigError.for_missing_env_vars(["PLAID_SECRET"])
        if not config.plaid_env:
            raise ConfigError.for_missing_env_vars(["PLAID_ENV"])

        host = _resolve_host(config.plaid_env)
        configuration = plaid.Configuration(host=host)
        api_client = plaid.ApiClient(configuration=configuration)
        api_client.set_default_header(
            "PLAID-CLIENT-ID", config.plaid_client_id
        )
        api_client.set_default_header("PLAID-SECRET", config.plaid_secret)
        return cls(plaid_api.PlaidApi(api_client))

    def sync_transactions(
        self,
        access_token: str,
        cursor: str | None = None,
    ) -> SyncResult:
        """
        Fetch incremental transaction changes and return typed results.

        Calls the Plaid transactions/sync endpoint and translates the SDK
        response into internal typed models.  Pass the cursor from a prior
        SyncResult to resume from where the last sync left off; omit it
        for the initial sync.
        """
        kwargs: dict[str, Any] = {"access_token": access_token}
        if cursor is not None:
            kwargs["cursor"] = cursor
        request = TransactionsSyncRequest(**kwargs)
        response = self._api.transactions_sync(request)

        return SyncResult(
            added=tuple(_to_transaction_data(tx) for tx in response.added),
            modified=tuple(
                _to_transaction_data(tx) for tx in response.modified
            ),
            removed=tuple(
                _to_removed_transaction_data(tx) for tx in response.removed
            ),
            accounts=tuple(_to_account_data(acc) for acc in response.accounts),
            next_cursor=str(response.next_cursor),
            has_more=bool(response.has_more),
        )
