PRAGMA foreign_keys = ON;

CREATE TABLE crm_accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
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

INSERT INTO crm_accounts
    (id, name, region, scheduled_date, status, cancellation_reason)
VALUES
    ('crm-189', 'Cobalt Museum sponsorship', 'Northeast',
     '2026-09-29', 'active', NULL),
    ('crm-589', 'Cobalt Museum sponsorship prospect', 'Unassigned',
     '2026-09-29', 'active', NULL),
    ('crm-242', 'Orchard Gallery annual membership', 'West',
     '2026-10-04', 'cancelled', 'duplicate account request');

INSERT INTO settings (name, value) VALUES
    ('first_target_cancel_timeout_consumed', '0');
