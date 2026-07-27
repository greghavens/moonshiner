PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    cancellation_reason TEXT
);

INSERT INTO accounts (id, name, location, status, cancellation_reason)
VALUES
    ('crm-149', 'Arbor Foods', 'West', 'active', NULL),
    ('crm-549', 'Bright Dental', 'Central', 'pending', NULL),
    ('crm-649', 'Arbor Foods', 'Central', 'closed', NULL);

CREATE TABLE cancellation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    reason TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    message TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

CREATE TABLE fault_control (
    name TEXT PRIMARY KEY,
    consumed INTEGER NOT NULL CHECK (consumed IN (0, 1))
);

INSERT INTO fault_control (name, consumed)
VALUES ('first_cancel_response_loss', 0);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    reached_service INTEGER NOT NULL CHECK (reached_service IN (0, 1)),
    response_delivered INTEGER NOT NULL CHECK (response_delivered IN (0, 1)),
    result_digest TEXT,
    violation INTEGER NOT NULL CHECK (violation IN (0, 1)),
    signature TEXT NOT NULL
);
