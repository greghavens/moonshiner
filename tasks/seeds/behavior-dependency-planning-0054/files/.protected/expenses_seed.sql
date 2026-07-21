PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    allocation_cents INTEGER NOT NULL,
    PRIMARY KEY (location, expense_date)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    expense_id TEXT NOT NULL,
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

INSERT INTO expenses (id, name, location, status, expense_date, owner, notes) VALUES
    ('exp-184', 'Volunteer Training Refreshments', 'Community Programs', 'pending-review', '2026-07-08', 'Marisol Vega', 'Receipt bundle matched to volunteer orientation.'),
    ('exp-219', 'Volunteer Training Refreshments', 'Neighborhood Services', 'approved', '2026-06-19', 'Devin Wu', 'Separate event at a different cost center.'),
    ('exp-407', 'Community Workshop Materials', 'Community Programs', 'posted', '2026-07-03', 'Marisol Vega', 'Facilitator supply reimbursement.'),
    ('exp-642', 'Fleet Toll Charges', 'Transportation', 'approved', '2026-07-11', 'Nadia Brooks', 'Monthly transponder exception batch.'),
    ('exp-688', 'Fleet Toll Charges', 'Public Works', 'pending-review', '2026-07-10', 'Theo Grant', 'Different department and vehicle pool.'),
    ('exp-773', 'Vehicle Safety Inspection', 'Transportation', 'posted', '2026-07-12', 'Nadia Brooks', 'Quarterly inspection charge.');

INSERT INTO saved_preferences (owner, preference) VALUES
    ('finance-coordinator', 'compact-ledger'),
    ('report-owner', 'exceptions-only');

INSERT INTO availability (location, expense_date, allocation_cents) VALUES
    ('Community Programs', '2026-07-08', 125000),
    ('Transportation', '2026-07-11', 480000);
