PRAGMA foreign_keys = ON;

CREATE TABLE inventory_items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
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

INSERT INTO inventory_items
    (id, name, location, scheduled_date, status, cancellation_reason)
VALUES
    ('inv-187', 'Nitrile examination gloves', 'Clinic Store',
     '2026-09-12', 'active', NULL),
    ('inv-587', 'Nitrile examination gloves recalled lot', 'Quarantine',
     '2026-09-12', 'active', NULL),
    ('inv-242', 'Sterile gauze packs', 'Surgical Supply',
     '2026-09-16', 'cancelled', 'duplicate replenishment request');

INSERT INTO settings (name, value) VALUES
    ('first_target_cancel_timeout_consumed', '0');
