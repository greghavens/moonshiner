PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    account_date TEXT,
    status TEXT,
    owner TEXT NOT NULL,
    segment TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

INSERT INTO accounts
    (id, name, location, account_date, status, owner, segment, internal_note)
VALUES
    ('acct-GCP-2048', 'Grove Clinic Partners', 'Pacific Region',
     '2026-07-15', 'Qualification', 'M. Reyes', 'Healthcare',
     'Pipeline scoring details are internal.'),
    ('acct-HBC-7713', 'Harbor Books Collective', 'Coastal Region',
     NULL, 'Contracting', 'T. Okafor', 'Retail',
     'Renewal history is internal.'),
    ('acct-GCP-3104', 'Grove Clinic Partners', 'Mountain Region',
     '2026-06-08', 'Active', 'K. Shah', 'Healthcare',
     'Location distractor.'),
    ('acct-GCP-4922', 'Grove Clinic Partners - Archive', 'Pacific Region',
     '2024-02-11', 'Archived', 'M. Reyes', 'Healthcare',
     'Historical-name distractor.'),
    ('acct-HBC-6380', 'Harbor Books Collective', 'Central Region',
     '2026-05-19', 'Prospecting', 'J. Lin', 'Retail',
     'Location distractor.'),
    ('acct-HBC-8890', 'Harbor Books Collective Legacy', 'Coastal Region',
     '2023-12-01', 'Archived', 'T. Okafor', 'Retail',
     'Historical-name distractor.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('account-team', 'pipeline-review');

CREATE TABLE availability (
    location TEXT PRIMARY KEY,
    review_slots INTEGER NOT NULL
);

INSERT INTO availability (location, review_slots)
VALUES ('Pacific Region', 3), ('Coastal Region', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    signature TEXT
);
