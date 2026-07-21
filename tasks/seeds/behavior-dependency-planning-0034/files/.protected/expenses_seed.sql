PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO expenses
    (id, name, location, expense_date, status, amount_cents, coordinator, notes)
VALUES
    ('exp-418', 'Exhibit Mounting Supplies', 'Museum Program',
     '2026-06-18', 'approved', 18475, 'Mara Singh',
     'Current exception-ledger item for exhibit preparation.'),
    ('exp-672', 'Clinic Parking Reimbursement', 'Patient Access',
     '2026-06-19', 'pending-review', 3600, 'Jon Bell',
     'Current reimbursement submitted for coordinator review.'),
    ('exp-184', 'Exhibit Mounting Supplies', 'Museum Program Annex',
     '2025-06-12', 'archived', 16240, 'Mara Singh',
     'Historical annex purchase retained for annual reporting.'),
    ('exp-281', 'Exhibit Mounting Supplies - 2025', 'Museum Program',
     '2025-06-14', 'archived', 17125, 'Dana Cole',
     'Historical similarly named program entry.'),
    ('exp-367', 'Clinic Parking Reimbursement', 'Patient Services',
     '2025-05-03', 'archived', 2400, 'Jon Bell',
     'Historical reimbursement from a different location.'),
    ('exp-526', 'Clinic Parking Reimbursement (Historical)', 'Patient Access',
     '2025-05-05', 'archived', 2800, 'Ari Gomez',
     'Historical similarly named patient-access entry.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('exception-ledger', 'show-current-fiscal-year');

CREATE TABLE availability (
    location TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    reviewer_slots INTEGER NOT NULL,
    PRIMARY KEY (location, expense_date)
);

INSERT INTO availability (location, expense_date, reviewer_slots)
VALUES
    ('Museum Program', '2026-06-18', 2),
    ('Patient Access', '2026-06-19', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id TEXT NOT NULL,
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
