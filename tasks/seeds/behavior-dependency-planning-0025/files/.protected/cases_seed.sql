PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    case_date TEXT,
    status TEXT,
    owner TEXT NOT NULL,
    customer_note TEXT NOT NULL
);

INSERT INTO cases
    (id, name, location, case_date, status, owner, customer_note)
VALUES
    ('sup-125', 'Kiosk Login Loop', 'Civic Museum',
     '2026-08-01', 'investigating', 'Nora Ellis',
     'Authentication loop reproduced on the lobby kiosk.'),
    ('sup-525', 'Receipt Export Missing Rows', 'Delta Housing',
     NULL, 'escalated', 'Mateo Ruiz',
     'CSV export omits rows when the monthly filter is active.'),
    ('sup-925', 'Kiosk Login Loop', 'Civic Museum Test',
     '2025-08-01', 'resolved', 'Archive Queue',
     'Historical test-environment case retained for audit.'),
    ('sup-825', 'Receipt Export Missing Rows', 'Delta Housing Sandbox',
     '2025-08-02', 'resolved', 'Archive Queue',
     'Historical sandbox case retained for audit.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('duty-manager', 'shift-handoff');

CREATE TABLE availability (
    location TEXT NOT NULL,
    support_date TEXT NOT NULL,
    staff_available INTEGER NOT NULL,
    PRIMARY KEY (location, support_date)
);

INSERT INTO availability (location, support_date, staff_available)
VALUES
    ('Civic Museum', '2026-08-01', 1),
    ('Delta Housing', '2026-08-01', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
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
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
