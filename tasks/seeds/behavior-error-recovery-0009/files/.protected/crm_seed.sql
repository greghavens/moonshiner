PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    status TEXT NOT NULL,
    canonical_id TEXT,
    renewal_month TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO accounts
    (id, name, region, status, canonical_id, renewal_month, notes)
VALUES
    ('crm-279', 'Lumen Literacy Network — Duplicate', 'Central Region',
     'active', 'crm-618', '2026-08',
     'Duplicate confirmed by the data steward before renewal export.'),
    ('crm-618', 'Lumen Literacy Network', 'Central Region',
     'active', NULL, '2026-08',
     'Canonical customer account retained for renewal processing.'),
    ('crm-845', 'Lumen Literacy Network', 'Northeast Region',
     'active', NULL, '2026-10',
     'Independent regional customer account.'),
    ('crm-114', 'Juniper Reading Cooperative', 'Central Region',
     'active', NULL, '2026-09',
     'Unrelated customer account.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('data-steward', 'renewal-export=canonical-only');

CREATE TABLE renewal_availability (
    account_id TEXT NOT NULL,
    export_date TEXT NOT NULL,
    available INTEGER NOT NULL,
    PRIMARY KEY (account_id, export_date)
);

INSERT INTO renewal_availability (account_id, export_date, available)
VALUES
    ('crm-618', '2026-08-01', 1),
    ('crm-845', '2026-10-01', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    account_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    committed INTEGER NOT NULL DEFAULT 0,
    result_digest TEXT,
    error_kind TEXT
);
