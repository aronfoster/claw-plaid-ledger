"""
Tests for the Plaid adapter boundary.

The error-path tests do import plaid to construct ApiException instances;
all other tests mock the SDK entirely via MagicMock.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import plaid  # type: ignore[import-untyped]
import pytest

from claw_plaid_ledger.config import Config, ConfigError
from claw_plaid_ledger.plaid_adapter import (
    SUPPORTED_ENVIRONMENTS,
    PlaidClientAdapter,
)
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    plaid_client_id: str | None = "test-client-id",
    plaid_secret: str | None = "test-secret",  # noqa: S107
    plaid_env: str | None = "sandbox",
    plaid_access_token: str | None = "fixture-token",  # noqa: S107
) -> Config:
    """Return a Config with valid Plaid credentials by default."""
    return Config(
        db_path=Path("ledger.db"),
        workspace_path=None,
        plaid_client_id=plaid_client_id,
        plaid_secret=plaid_secret,
        plaid_env=plaid_env,
        plaid_access_token=plaid_access_token,
    )


def _raw_account(
    *,
    account_id: str = "acc-1",
    name: str = "Checking",
    type: str = "depository",  # noqa: A002
    subtype: str | None = "checking",
    mask: str | None = "0000",
) -> MagicMock:
    """Return a mock object that mimics a Plaid SDK AccountBase."""
    raw = MagicMock()
    raw.account_id = account_id
    raw.name = name
    raw.type = type
    raw.subtype = subtype
    raw.mask = mask
    return raw


def _raw_transaction(  # noqa: PLR0913
    *,
    transaction_id: str = "tx-1",
    account_id: str = "acc-1",
    amount: float = 9.99,
    date: datetime.date = datetime.date(2024, 3, 1),
    name: str = "Coffee Shop",
    pending: bool = False,
    merchant_name: str | None = "Bean Co",
    iso_currency_code: str | None = "USD",
) -> MagicMock:
    """Return a mock object that mimics a Plaid SDK Transaction."""
    raw = MagicMock()
    raw.transaction_id = transaction_id
    raw.account_id = account_id
    raw.amount = amount
    raw.date = date
    raw.name = name
    raw.pending = pending
    raw.merchant_name = merchant_name
    raw.iso_currency_code = iso_currency_code
    return raw


def _raw_removed(transaction_id: str = "tx-old") -> MagicMock:
    """Return a mock object that mimics a Plaid SDK RemovedTransaction."""
    raw = MagicMock()
    raw.transaction_id = transaction_id
    return raw


def _raw_sync_response(  # noqa: PLR0913
    *,
    added: list[MagicMock] | None = None,
    modified: list[MagicMock] | None = None,
    removed: list[MagicMock] | None = None,
    accounts: list[MagicMock] | None = None,
    next_cursor: str = "cursor-xyz",
    has_more: bool = False,
) -> MagicMock:
    """Return a mock Plaid transactions/sync SDK response."""
    resp = MagicMock()
    resp.added = added or []
    resp.modified = modified or []
    resp.removed = removed or []
    resp.accounts = accounts or []
    resp.next_cursor = next_cursor
    resp.has_more = has_more
    return resp


def _adapter_with_mock_api() -> tuple[PlaidClientAdapter, MagicMock]:
    """Return an adapter wired to a MagicMock API, and the mock itself."""
    api_mock = MagicMock()
    return PlaidClientAdapter(api_mock), api_mock


# ---------------------------------------------------------------------------
# SUPPORTED_ENVIRONMENTS
# ---------------------------------------------------------------------------


def test_supported_environments_contains_expected() -> None:
    """SUPPORTED_ENVIRONMENTS includes sandbox and production."""
    assert "sandbox" in SUPPORTED_ENVIRONMENTS
    assert "production" in SUPPORTED_ENVIRONMENTS


# ---------------------------------------------------------------------------
# PlaidClientAdapter.from_config
# ---------------------------------------------------------------------------


def test_from_config_returns_adapter_instance() -> None:
    """from_config succeeds with valid credentials and returns the adapter."""
    adapter = PlaidClientAdapter.from_config(_make_config())
    assert isinstance(adapter, PlaidClientAdapter)


def test_from_config_production_env() -> None:
    """from_config succeeds with production environment."""
    adapter = PlaidClientAdapter.from_config(
        _make_config(plaid_env="production")
    )
    assert isinstance(adapter, PlaidClientAdapter)


def test_from_config_env_is_case_insensitive() -> None:
    """from_config accepts mixed-case PLAID_ENV values."""
    adapter = PlaidClientAdapter.from_config(_make_config(plaid_env="Sandbox"))
    assert isinstance(adapter, PlaidClientAdapter)


def test_from_config_missing_client_id_raises() -> None:
    """from_config raises ConfigError when PLAID_CLIENT_ID is absent."""
    with pytest.raises(ConfigError, match="PLAID_CLIENT_ID"):
        PlaidClientAdapter.from_config(_make_config(plaid_client_id=None))


def test_from_config_missing_secret_raises() -> None:
    """from_config raises ConfigError when PLAID_SECRET is absent."""
    with pytest.raises(ConfigError, match="PLAID_SECRET"):
        PlaidClientAdapter.from_config(_make_config(plaid_secret=None))


def test_from_config_missing_env_raises() -> None:
    """from_config raises ConfigError when PLAID_ENV is absent."""
    with pytest.raises(ConfigError, match="PLAID_ENV"):
        PlaidClientAdapter.from_config(_make_config(plaid_env=None))


def test_from_config_unsupported_env_raises() -> None:
    """from_config raises ConfigError for an unrecognized PLAID_ENV."""
    with pytest.raises(
        ConfigError, match="Unsupported PLAID_ENV 'development'"
    ):
        PlaidClientAdapter.from_config(_make_config(plaid_env="development"))


# ---------------------------------------------------------------------------
# sync_transactions — request construction
# ---------------------------------------------------------------------------


def test_sync_transactions_omits_cursor_on_initial_sync() -> None:
    """sync_transactions sends no cursor kwarg when cursor is None."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response()

    with patch(
        "claw_plaid_ledger.plaid_adapter.TransactionsSyncRequest"
    ) as mock_req_cls:
        mock_req_cls.return_value = MagicMock()
        adapter.sync_transactions("access-token")

    mock_req_cls.assert_called_once_with(
        access_token="access-token"  # noqa: S106
    )


def test_sync_transactions_includes_cursor_when_provided() -> None:
    """sync_transactions passes the cursor kwarg when one is given."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response()

    with patch(
        "claw_plaid_ledger.plaid_adapter.TransactionsSyncRequest"
    ) as mock_req_cls:
        mock_req_cls.return_value = MagicMock()
        adapter.sync_transactions("access-token", cursor="prev-cursor")

    mock_req_cls.assert_called_once_with(
        access_token="access-token",  # noqa: S106
        cursor="prev-cursor",
    )


# ---------------------------------------------------------------------------
# sync_transactions — response translation
# ---------------------------------------------------------------------------


def test_sync_transactions_returns_sync_result() -> None:
    """sync_transactions returns a SyncResult."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response()

    result = adapter.sync_transactions("tok")
    assert isinstance(result, SyncResult)


def test_sync_transactions_translates_added_transaction() -> None:
    """Added transactions are translated into TransactionData correctly."""
    expected_amount = 42.50
    expected_date = datetime.date(2024, 1, 15)

    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response(
        added=[
            _raw_transaction(
                transaction_id="tx-abc",
                account_id="acc-1",
                amount=expected_amount,
                date=expected_date,
                name="STARBUCKS",
                pending=False,
                merchant_name="Starbucks",
                iso_currency_code="USD",
            )
        ]
    )

    result = adapter.sync_transactions("tok")

    assert len(result.added) == 1
    tx = result.added[0]
    assert isinstance(tx, TransactionData)
    assert tx.plaid_transaction_id == "tx-abc"
    assert tx.plaid_account_id == "acc-1"
    assert tx.amount == expected_amount
    assert tx.date == expected_date
    assert tx.name == "STARBUCKS"
    assert tx.pending is False
    assert tx.merchant_name == "Starbucks"
    assert tx.iso_currency_code == "USD"


def test_sync_transactions_translates_account() -> None:
    """Accounts are translated into AccountData correctly."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response(
        accounts=[
            _raw_account(
                account_id="acc-1",
                name="Checking",
                type="depository",
                subtype="checking",
                mask="1234",
            )
        ]
    )

    result = adapter.sync_transactions("tok")

    assert len(result.accounts) == 1
    acc = result.accounts[0]
    assert isinstance(acc, AccountData)
    assert acc.plaid_account_id == "acc-1"
    assert acc.name == "Checking"
    assert acc.type == "depository"
    assert acc.subtype == "checking"
    assert acc.mask == "1234"


def test_sync_transactions_translates_removed_transaction() -> None:
    """Removed transactions are translated into RemovedTransactionData."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response(
        removed=[_raw_removed("tx-gone")]
    )

    result = adapter.sync_transactions("tok")

    assert len(result.removed) == 1
    assert isinstance(result.removed[0], RemovedTransactionData)
    assert result.removed[0].plaid_transaction_id == "tx-gone"


def test_sync_transactions_preserves_cursor_and_has_more() -> None:
    """next_cursor and has_more are propagated from the SDK response."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response(
        next_cursor="next-cur", has_more=True
    )

    result = adapter.sync_transactions("tok")
    assert result.next_cursor == "next-cur"
    assert result.has_more is True


def test_sync_transactions_handles_null_optional_fields() -> None:
    """None optional fields on transactions and accounts are preserved."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response(
        added=[_raw_transaction(merchant_name=None, iso_currency_code=None)],
        accounts=[_raw_account(subtype=None, mask=None)],
    )

    result = adapter.sync_transactions("tok")
    assert result.added[0].merchant_name is None
    assert result.added[0].iso_currency_code is None
    assert result.accounts[0].subtype is None
    assert result.accounts[0].mask is None


def test_sync_transactions_empty_response() -> None:
    """Empty added/modified/removed/accounts lists produce empty tuples."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response()

    result = adapter.sync_transactions("tok")
    assert result.added == ()
    assert result.modified == ()
    assert result.removed == ()
    assert result.accounts == ()


def test_sync_transactions_translates_modified_transaction() -> None:
    """Modified transactions are translated just like added ones."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.transactions_sync.return_value = _raw_sync_response(
        modified=[_raw_transaction(transaction_id="tx-mod")]
    )

    result = adapter.sync_transactions("tok")
    assert len(result.modified) == 1
    assert isinstance(result.modified[0], TransactionData)
    assert result.modified[0].plaid_transaction_id == "tx-mod"


def test_sync_transactions_date_as_string_is_parsed() -> None:
    """A date arriving as an ISO string is coerced to datetime.date."""
    adapter, api_mock = _adapter_with_mock_api()
    raw = _raw_transaction()
    raw.date = "2024-06-15"  # string instead of datetime.date
    api_mock.transactions_sync.return_value = _raw_sync_response(added=[raw])

    result = adapter.sync_transactions("tok")
    assert result.added[0].date == datetime.date(2024, 6, 15)


# ---------------------------------------------------------------------------
# create_link_token
# ---------------------------------------------------------------------------


def test_create_link_token_returns_link_token() -> None:
    """create_link_token returns the link_token string from the SDK."""
    adapter, api_mock = _adapter_with_mock_api()
    resp = MagicMock()
    resp.link_token = "link-sandbox-abc123"  # noqa: S105
    api_mock.link_token_create.return_value = resp

    result = adapter.create_link_token(
        user_client_id="operator",
        products=["transactions"],
        country_codes=["US"],
    )

    assert result == "link-sandbox-abc123"
    api_mock.link_token_create.assert_called_once()


def test_create_link_token_transient_error_429() -> None:
    """create_link_token raises PlaidTransientError on HTTP 429."""
    adapter, api_mock = _adapter_with_mock_api()
    exc = plaid.ApiException(status=429)
    api_mock.link_token_create.side_effect = exc

    with pytest.raises(PlaidTransientError):
        adapter.create_link_token(
            user_client_id="operator",
            products=["transactions"],
            country_codes=["US"],
        )


def test_create_link_token_permanent_error_400() -> None:
    """create_link_token raises PlaidPermanentError on HTTP 400."""
    adapter, api_mock = _adapter_with_mock_api()
    exc = plaid.ApiException(status=400)
    api_mock.link_token_create.side_effect = exc

    with pytest.raises(PlaidPermanentError):
        adapter.create_link_token(
            user_client_id="operator",
            products=["transactions"],
            country_codes=["US"],
        )


def test_create_link_token_network_error_is_transient() -> None:
    """create_link_token raises PlaidTransientError on OSError."""
    adapter, api_mock = _adapter_with_mock_api()
    api_mock.link_token_create.side_effect = OSError("connection refused")

    with pytest.raises(PlaidTransientError):
        adapter.create_link_token(
            user_client_id="operator",
            products=["transactions"],
            country_codes=["US"],
        )


# ---------------------------------------------------------------------------
# exchange_public_token
# ---------------------------------------------------------------------------


def test_exchange_public_token_returns_access_token_and_item_id() -> None:
    """exchange_public_token returns (access_token, item_id) from the SDK."""
    adapter, api_mock = _adapter_with_mock_api()
    resp = MagicMock()
    resp.access_token = "access-sandbox-xyz"  # noqa: S105
    resp.item_id = "item-abc123"
    api_mock.item_public_token_exchange.return_value = resp

    access_token, item_id = adapter.exchange_public_token("public-sandbox-tok")

    assert access_token == "access-sandbox-xyz"  # noqa: S105
    assert item_id == "item-abc123"
    api_mock.item_public_token_exchange.assert_called_once()


def test_exchange_public_token_transient_error_500() -> None:
    """exchange_public_token raises PlaidTransientError on HTTP 500."""
    adapter, api_mock = _adapter_with_mock_api()
    exc = plaid.ApiException(status=500)
    api_mock.item_public_token_exchange.side_effect = exc

    with pytest.raises(PlaidTransientError):
        adapter.exchange_public_token("public-tok")


def test_exchange_public_token_permanent_error_400() -> None:
    """exchange_public_token raises PlaidPermanentError on HTTP 400."""
    adapter, api_mock = _adapter_with_mock_api()
    exc = plaid.ApiException(status=400)
    api_mock.item_public_token_exchange.side_effect = exc

    with pytest.raises(PlaidPermanentError):
        adapter.exchange_public_token("public-tok")
