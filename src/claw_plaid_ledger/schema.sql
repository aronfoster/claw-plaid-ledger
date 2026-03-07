CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    plaid_account_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    mask TEXT,
    type TEXT,
    subtype TEXT,
    institution_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    plaid_transaction_id TEXT NOT NULL UNIQUE,
    plaid_account_id TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    iso_currency_code TEXT,
    name TEXT NOT NULL,
    merchant_name TEXT,
    pending INTEGER NOT NULL,
    authorized_date TEXT,
    posted_date TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY,
    cursor TEXT,
    updated_at TEXT NOT NULL
);
