PRAGMA foreign_keys = ON;

CREATE TABLE facilities_requests (
    id TEXT PRIMARY KEY,
    request_name TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    location TEXT NOT NULL,
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

INSERT INTO facilities_requests (
    id, request_name, scheduled_for, location, status, cancellation_reason
) VALUES
    ('fac-177', 'East stairwell lighting repair', '2026-08-11T07:30:00-06:00', 'Cedar Building — east stairwell', 'active', NULL),
    ('fac-717', 'East stairwell lighting repair — annex', '2026-08-12T07:30:00-06:00', 'Cedar Annex — east stairwell', 'active', NULL),
    ('fac-248', 'Loading dock door inspection', '2026-08-13T10:00:00-06:00', 'Cedar Building — loading dock', 'active', NULL);

INSERT INTO settings (name, value) VALUES
    ('first_cancel_timeout_consumed', '0');
