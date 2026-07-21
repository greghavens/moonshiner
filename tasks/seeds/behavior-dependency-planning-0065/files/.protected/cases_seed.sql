PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_date TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

INSERT INTO cases
    (id, name, location, status, opened_date, priority, owner, internal_note)
VALUES
    ('sup-165', 'Mobile Check-In Failure', 'Grove Clinics', 'investigating',
     '2026-10-21', 'high', 'Mobile Support', 'Escalation details are internal.'),
    ('sup-565', 'Address Validation Rejection', 'Harbor Books', 'pending-customer',
     '2026-10-22', 'normal', 'Commerce Support', 'Customer context is internal.'),
    ('sup-965', 'Mobile Check-In Failure', 'Grove Clinics Demo', 'resolved',
     '2025-10-21', 'low', 'Demo Support', 'Demo tenant distractor.'),
    ('sup-765', 'Mobile Check-In Failure - Legacy', 'Grove Clinics', 'closed',
     '2025-08-12', 'low', 'Archive Support', 'Historical name distractor.'),
    ('sup-865', 'Address Validation Rejection', 'Harbor Books Sandbox', 'resolved',
     '2025-10-22', 'low', 'Sandbox Support', 'Sandbox tenant distractor.'),
    ('sup-665', 'Address Validation Rejection (2025)', 'Harbor Books', 'closed',
     '2025-07-19', 'low', 'Archive Support', 'Historical name distractor.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('duty-manager', 'compact-handoff');

CREATE TABLE availability (
    location TEXT PRIMARY KEY,
    responder_count INTEGER NOT NULL
);

INSERT INTO availability (location, responder_count)
VALUES ('Grove Clinics', 3), ('Harbor Books', 2);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
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
    parent_exe TEXT NOT NULL,
    parent_started_ticks INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    signature TEXT
);
