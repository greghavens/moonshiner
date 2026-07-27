PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    case_date TEXT NOT NULL
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    case_id TEXT NOT NULL,
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
    violation INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT
);

INSERT INTO cases (id, name, location, status, case_date) VALUES
    ('sup-305', 'Damaged exhibit shipment', 'Museum Desk', 'open', '2026-08-05'),
    ('sup-306', 'Damaged exhibit shipment', 'Warehouse Desk', 'in-review', '2026-08-06'),
    ('sup-307', 'Damaged exhibit shipment follow-up', 'Museum Desk', 'archived', '2026-08-05'),
    ('sup-704', 'Duplicate membership charges', 'Billing Desk', 'resolved', '2026-08-04'),
    ('sup-705', 'Duplicate membership charge', 'Billing Desk', 'awaiting-customer', '2026-08-07'),
    ('sup-706', 'Duplicate membership charge', 'Membership Desk', 'open', '2026-08-08');
