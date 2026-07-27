PRAGMA foreign_keys = ON;

CREATE TABLE records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'closed', 'cancelled')),
    cancellation_reason TEXT
);

CREATE TABLE cancellation_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('timed_out_before_commit', 'committed'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO records
    (stable_id, name, location, status, cancellation_reason)
VALUES
    ('pub-159', 'Pet license', 'Aurora', 'active', NULL),
    ('pub-559', 'Facility permit', 'Lakewood', 'pending', NULL),
    ('pub-659', 'Pet license', 'Lakewood', 'closed', NULL);
