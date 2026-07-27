PRAGMA foreign_keys = ON;

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'pending', 'cancelled', 'closed', 'completed')
    ),
    cancellation_reason TEXT,
    version INTEGER NOT NULL,
    cancellation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES requests(id),
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE service_control (
    request_id TEXT NOT NULL REFERENCES requests(id),
    fault TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY (request_id, fault)
);

INSERT INTO requests (
    id, name, location, scheduled_for, status,
    cancellation_reason, version, cancellation_count
) VALUES
    (
        'fac-157', 'Lighting repair', 'Building A',
        '2026-08-18T10:00:00-06:00', 'active', NULL, 7, 0
    ),
    (
        'fac-657', 'Lighting repair', 'Building B',
        '2026-06-03T13:30:00-06:00', 'closed', NULL, 9, 0
    ),
    (
        'fac-557', 'Room setup', 'Building B',
        '2026-08-21T08:00:00-06:00', 'pending', NULL, 3, 0
    ),
    (
        'fac-757', 'Lighting repair follow-up', 'Building A',
        '2026-08-19T15:00:00-06:00', 'active', NULL, 2, 0
    );

INSERT INTO service_control (request_id, fault, remaining)
VALUES ('fac-157', 'cancel_deadline_after_commit', 1);
