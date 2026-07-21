PRAGMA foreign_keys = ON;

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    claim_date TEXT NOT NULL,
    policyholder TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    review_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (location, review_date)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    claim_id TEXT NOT NULL,
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

INSERT INTO claims
    (id, name, location, status, claim_date, policyholder, notes)
VALUES
    ('ins-156', 'Travel Delay Claim', 'International Desk', 'awaiting-documents', '2026-09-19', 'Mara Ellison', 'Carrier confirmation has not yet been attached.'),
    ('ins-956', 'Travel Delay Claim', 'International Desk Archive', 'closed', '2025-09-19', 'Jon Bell', 'Archived claim with a similar desk name.'),
    ('ins-315', 'Baggage Damage Claim', 'International Desk', 'under-review', '2026-09-17', 'Nadia Cole', 'Different claim at the same desk.'),
    ('ins-556', 'Equipment Transit Claim', 'Logistics Desk', 'adjuster-assigned', '2026-09-20', 'Arc Light Events', 'Field adjuster assignment is recorded.'),
    ('ins-856', 'Equipment Transit Claim', 'Logistics Desk Closed Files', 'closed', '2025-09-20', 'Northwind Audio', 'Closed-file claim with a similar desk name.'),
    ('ins-742', 'Warehouse Handling Claim', 'Logistics Desk', 'intake-complete', '2026-09-18', 'Sable Freight', 'Different claim at the same desk.');

INSERT INTO saved_preferences (owner, preference) VALUES
    ('claims-coordinator', 'review-queue-summary'),
    ('report-owner', 'status-only');

INSERT INTO availability (location, review_date, open_slots) VALUES
    ('International Desk', '2026-09-22', 2),
    ('Logistics Desk', '2026-09-22', 4);
