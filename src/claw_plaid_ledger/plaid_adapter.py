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
from plaid.model.country_code import (  # type: ignore[import-untyped]
    CountryCode,
)
from plaid.model.item_public_token_exchange_request import (  # type: ignore[import-untyped]
    ItemPublicTokenExchangeRequest,
)
from plaid.model.item_webhook_update_request import (  # type: ignore[import-untyped]
    ItemWebhookUpdateRequest,
)
from plaid.model.link_token_create_request import (  # type: ignore[import-untyped]
    LinkTokenCreateRequest,
)
from plaid.model.link_token_create_request_user import (  # type: ignore[import-untyped]
    LinkTokenCreateRequestUser,
)
from plaid.model.products import (  # type: ignore[import-untyped]
    Products,
)
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
from claw_plaid_ledger.sync_engine import (
    PlaidPermanentError,
    PlaidTransientError,
)

_ENV_MAP: dict[str, str] = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

# HTTP status code thresholds for transient-vs-permanent classification.
_HTTP_TOO_MANY_REQUESTS: int = 429
_HTTP_SERVER_ERROR_MIN: int = 500

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
        try:
            response = self._api.transactions_sync(request)
        except plaid.ApiException as exc:
            # HTTP 429 (rate limit) and 5xx (server error) are transient;
            # other 4xx responses (e.g. INVALID_ACCESS_TOKEN) are permanent.
            status: int = getattr(exc, "status", 0)
            if (
                status == _HTTP_TOO_MANY_REQUESTS
                or status >= _HTTP_SERVER_ERROR_MIN
            ):
                msg = f"Plaid transient API error (HTTP {status}): {exc}"
                raise PlaidTransientError(msg) from exc
            msg = f"Plaid permanent API error (HTTP {status}): {exc}"
            raise PlaidPermanentError(msg) from exc
        except OSError as exc:
            msg = f"Network error calling Plaid: {exc}"
            raise PlaidTransientError(msg) from exc

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

    def create_link_token(
        self,
        user_client_id: str,
        products: list[str],
        country_codes: list[str],
        webhook: str | None = None,
    ) -> str:
        """
        Create a Plaid Link token for the browser Link flow.

        Calls the Plaid /link/token/create endpoint and returns the
        ``link_token`` string for use in the Plaid Link JS initializer.

        Pass ``webhook`` to register a webhook URL on items created through
        this link session.  Plaid will deliver transaction events to that URL
        without requiring a separate ``/item/webhook/update`` call.
        """
        kwargs: dict[str, Any] = {
            "products": [Products(p) for p in products],
            "client_name": "claw-plaid-ledger",
            "country_codes": [CountryCode(c) for c in country_codes],
            "language": "en",
            "user": LinkTokenCreateRequestUser(client_user_id=user_client_id),
        }
        if webhook is not None:
            kwargs["webhook"] = webhook
        request = LinkTokenCreateRequest(**kwargs)
        try:
            response = self._api.link_token_create(request)
        except plaid.ApiException as exc:
            status: int = getattr(exc, "status", 0)
            if (
                status == _HTTP_TOO_MANY_REQUESTS
                or status >= _HTTP_SERVER_ERROR_MIN
            ):
                msg = f"Plaid transient API error (HTTP {status}): {exc}"
                raise PlaidTransientError(msg) from exc
            msg = f"Plaid permanent API error (HTTP {status}): {exc}"
            raise PlaidPermanentError(msg) from exc
        except OSError as exc:
            msg = f"Network error calling Plaid: {exc}"
            raise PlaidTransientError(msg) from exc
        return str(response.link_token)

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        """
        Exchange a Plaid public token for an access token and item ID.

        Calls the Plaid /item/public_token/exchange endpoint and returns
        ``(access_token, item_id)``.
        """
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        try:
            response = self._api.item_public_token_exchange(request)
        except plaid.ApiException as exc:
            status = getattr(exc, "status", 0)
            if (
                status == _HTTP_TOO_MANY_REQUESTS
                or status >= _HTTP_SERVER_ERROR_MIN
            ):
                msg = f"Plaid transient API error (HTTP {status}): {exc}"
                raise PlaidTransientError(msg) from exc
            msg = f"Plaid permanent API error (HTTP {status}): {exc}"
            raise PlaidPermanentError(msg) from exc
        except OSError as exc:
            msg = f"Network error calling Plaid: {exc}"
            raise PlaidTransientError(msg) from exc
        return str(response.access_token), str(response.item_id)

    def update_item_webhook(self, access_token: str, webhook: str) -> None:
        """
        Update the webhook URL registered on an existing Plaid item.

        Calls the Plaid /item/webhook/update endpoint.  Use this to register
        or change the webhook URL for items that were linked before a webhook
        was configured (or when the server URL changes).
        """
        request = ItemWebhookUpdateRequest(
            access_token=access_token,
            webhook=webhook,
        )
        try:
            self._api.item_webhook_update(request)
        except plaid.ApiException as exc:
            status = getattr(exc, "status", 0)
            if (
                status == _HTTP_TOO_MANY_REQUESTS
                or status >= _HTTP_SERVER_ERROR_MIN
            ):
                msg = f"Plaid transient API error (HTTP {status}): {exc}"
                raise PlaidTransientError(msg) from exc
            msg = f"Plaid permanent API error (HTTP {status}): {exc}"
            raise PlaidPermanentError(msg) from exc
        except OSError as exc:
            msg = f"Network error calling Plaid: {exc}"
            raise PlaidTransientError(msg) from exc
