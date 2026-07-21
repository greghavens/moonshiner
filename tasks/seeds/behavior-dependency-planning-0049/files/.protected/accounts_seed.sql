PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    service_tier TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

INSERT INTO accounts
    (id, name, location, status, record_date, owner, service_tier, internal_note)
VALUES
    ('acc-149', 'Elm Learning Network', 'Northeast Region', 'active',
     '2026-10-08', 'A. Rivera', 'education-plus',
     'Annual learning-network review is scheduled for the next quarter.'),
    ('acc-549', 'Frost Community Health', 'Mountain Region', 'renewal-review',
     '2026-10-04', 'M. Chen', 'community-care',
     'Renewal documents are being validated by the account owner.'),
    ('acc-949', 'Elm Learning Network', 'Northeast Annex', 'archived',
     '2025-10-08', 'Archive Team', 'legacy',
     'Historical annex record; not the Northeast Region account.'),
    ('acc-849', 'Frost Community Health', 'Mountain Region Archive', 'closed',
     '2025-10-04', 'Archive Team', 'legacy',
     'Historical regional record retained for audit history.'),
    ('acc-263', 'Elm Learning Networks', 'Northeast Region', 'prospect',
     '2026-08-11', 'B. Singh', 'standard',
     'Different organization with a pluralized legal name.'),
    ('acc-681', 'Frost Community Wellness', 'Mountain Region', 'active',
     '2026-08-12', 'K. James', 'standard',
     'Separate wellness organization in the same region.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('account-team', 'compact-pipeline-view');

CREATE TABLE availability (
    location TEXT NOT NULL,
    service_family TEXT NOT NULL,
    review_slots INTEGER NOT NULL,
    PRIMARY KEY (location, service_family)
);

INSERT INTO availability (location, service_family, review_slots)
VALUES
    ('Northeast Region', 'education', 3),
    ('Mountain Region', 'health', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
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
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
