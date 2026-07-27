PRAGMA foreign_keys = ON;

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    loss_date TEXT NOT NULL,
    adjuster TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO claims
    (id, name, location, status, loss_date, adjuster, notes)
VALUES
    ('ins-116', 'Theft claim', 'West Office', 'active',
     '2026-05-18', 'Morgan Lee', 'Police report received.'),
    ('ins-516', 'Windshield claim', 'North Office', 'pending',
     '2026-06-02', 'Avery Chen', 'Awaiting repair estimate.'),
    ('ins-916', 'Theft claim', 'North Office', 'closed',
     '2025-11-07', 'Jordan Patel', 'Payment issued and file closed.');

CREATE TABLE operator_profile (
    operator TEXT PRIMARY KEY,
    default_office TEXT NOT NULL,
    preferred_view TEXT NOT NULL
);

INSERT INTO operator_profile (operator, default_office, preferred_view)
VALUES ('claims-desk', 'West Office', 'summary');

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES claims(id)
);

CREATE TABLE audit_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    violation INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER,
    returned_id TEXT,
    output_json TEXT,
    error TEXT
);
