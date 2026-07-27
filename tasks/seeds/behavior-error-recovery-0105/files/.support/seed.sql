PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    customer TEXT NOT NULL,
    opened_on TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancelled', 'cancellation-pending')
    ),
    cancellation_reason TEXT,
    revision INTEGER NOT NULL CHECK (revision > 0)
);

CREATE TABLE service_state (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

INSERT INTO cases
    (stable_id, name, customer, opened_on, priority, status,
     cancellation_reason, revision)
VALUES
    ('sup-205', 'Missing conference badge', 'Northwind Events', '2026-06-09', 'normal', 'active', NULL, 1),
    ('sup-520', 'Missing conference badge follow-up', 'Northwind Events', '2026-06-10', 'normal', 'active', NULL, 1),
    ('sup-711', 'Damaged conference lanyard', 'City Arts Forum', '2026-06-11', 'low', 'active', NULL, 1);

INSERT INTO service_state (key, value) VALUES ('cancel_requests', 0);
