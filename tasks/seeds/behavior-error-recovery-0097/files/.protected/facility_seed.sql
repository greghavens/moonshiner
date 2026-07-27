PRAGMA foreign_keys = ON;

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    priority TEXT NOT NULL,
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

INSERT INTO requests
    (id, name, location, scheduled_date, priority, status, cancellation_reason)
VALUES
    ('fac-197', 'Loading dock door inspection', 'Warehouse', '2026-09-24',
     'standard', 'active', NULL),
    ('fac-597', 'Loading dock door inspection checklist', 'Safety Office',
     '2026-09-24', 'standard', 'active', NULL),
    ('fac-318', 'Freight elevator preventive service', 'Warehouse',
     '2026-09-19', 'high', 'cancelled', 'vendor unavailable');

INSERT INTO settings (name, value) VALUES
    ('first_target_cancel_timeout_consumed', '0');
