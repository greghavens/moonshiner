PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    queue TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancellation-pending', 'cancelled')
    ),
    cancellation_reason TEXT
);

CREATE TABLE settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE audit_events (
    sequence INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    seal TEXT NOT NULL
);

INSERT INTO cases
    (id, name, queue, scheduled_date, status, cancellation_reason)
VALUES
    ('sup-185', 'Learning portal access failure', 'Education Queue',
     '2026-09-14', 'active', NULL),
    ('sup-585', 'Learning portal access failure test', 'Internal QA',
     '2026-09-14', 'active', NULL),
    ('sup-247', 'Campus Wi-Fi password reset', 'General Support',
     '2026-09-18', 'cancelled', 'requester restored access');

INSERT INTO settings (name, value) VALUES
    ('first_cancel_timeout_consumed', '0');
