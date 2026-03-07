from plaid import ApiClient
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_sync_response import TransactionsSyncResponse

class PlaidApi:
    def __init__(self, api_client: ApiClient) -> None: ...
    def transactions_sync(
        self,
        request: TransactionsSyncRequest,
    ) -> TransactionsSyncResponse: ...
