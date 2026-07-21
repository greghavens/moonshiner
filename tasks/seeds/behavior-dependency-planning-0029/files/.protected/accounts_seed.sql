PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    review_date TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    segment TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO accounts
    (id, name, location, review_date, status, owner, segment, notes)
VALUES
    ('acct_129c4e71', 'Civic Museum Alliance', 'Midwest Region',
     '2026-08-13', 'onboarding', 'Jordan Lee', 'public-sector',
     'Handoff review scheduled before the pipeline meeting.'),
    ('acct_529d8a63', 'Delta Housing Trust', 'Southeast Region',
     '2026-08-14', 'active', 'Morgan Diaz', 'nonprofit',
     'Account is active in the current regional pipeline.'),
    ('acct_929b0f15', 'Civic Museum Alliance - 2025', 'Midwest Region',
     '2025-08-13', 'inactive', 'Jordan Lee', 'public-sector',
     'Historical entry retained for prior-year reporting.'),
    ('acct_2ad683e0', 'Civic Museum Alliance', 'Museum Partnerships',
     '2025-07-29', 'archived', 'Avery Chen', 'partnerships',
     'Historical alternate-location account.'),
    ('acct_829f341c', 'Delta Housing Trust (Legacy)', 'Southeast Region',
     '2025-08-14', 'inactive', 'Morgan Diaz', 'nonprofit',
     'Legacy record retained for pipeline reconciliation.'),
    ('acct_4ec9207b', 'Delta Housing Trust', 'Southeast Region Legacy',
     '2025-06-10', 'archived', 'Riley Patel', 'nonprofit',
     'Historical alternate-location account.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('account-handoff', 'pipeline-review');

CREATE TABLE availability (
    location TEXT NOT NULL,
    review_date TEXT NOT NULL,
    reviewer_slots INTEGER NOT NULL,
    PRIMARY KEY (location, review_date)
);

INSERT INTO availability (location, review_date, reviewer_slots)
VALUES
    ('Midwest Region', '2026-08-13', 2),
    ('Southeast Region', '2026-08-14', 3);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    action_id TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
