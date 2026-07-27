PRAGMA foreign_keys = ON;

CREATE TABLE inventory_items (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancelled', 'cancellation-pending')
    ),
    cancellation_reason TEXT,
    cancellation_requested_at TEXT,
    revision INTEGER NOT NULL CHECK (revision > 0)
);

CREATE TABLE service_state (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL CHECK (value >= 0)
);

INSERT INTO inventory_items
    (stable_id, name, location, scheduled_date, status, cancellation_reason,
     cancellation_requested_at, revision)
VALUES
    ('inv-207', 'Large-print program booklets', 'Events Store', '2026-10-10', 'active', NULL, NULL, 1),
    ('inv-607', 'Large-print program booklets proof', 'Print Room', '2026-10-10', 'active', NULL, NULL, 1),
    ('inv-311', 'Stage direction cue cards', 'Production', '2026-10-10', 'active', NULL, NULL, 1);

INSERT INTO service_state (key, value) VALUES
    ('cancel_requests', 0),
    ('timeout_after_commit_remaining', 1);
