PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    candidate_name TEXT NOT NULL,
    department TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'cancelled', 'closed')),
    cancellation_reason TEXT,
    version INTEGER NOT NULL,
    cancellation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE service_control (
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    fault TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY (candidate_id, fault)
);

INSERT INTO candidates (
    id, candidate_name, department, status, cancellation_reason,
    version, cancellation_count
) VALUES
    ('rec-151', 'Casey Evans', 'Sales', 'active', NULL, 7, 0),
    ('rec-551', 'Devon Flores', 'Research', 'pending', NULL, 2, 0),
    (
        'rec-651', 'Casey Evans', 'Research', 'closed',
        'position filled in prior cycle', 11, 0
    );

INSERT INTO service_control (candidate_id, fault, remaining)
VALUES ('rec-151', 'cancel_timeout_without_commit', 1);
