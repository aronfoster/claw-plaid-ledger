CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    plaid_account_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    mask TEXT,
    type TEXT,
    subtype TEXT,
    institution_name TEXT,
    owner TEXT,
    item_id TEXT,
    canonical_account_id TEXT,
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
    item_id TEXT NOT NULL UNIQUE,
    cursor TEXT,
    owner TEXT,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY,
    plaid_transaction_id TEXT NOT NULL UNIQUE
        REFERENCES transactions(plaid_transaction_id),
    category TEXT,
    note TEXT,
    tags TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
