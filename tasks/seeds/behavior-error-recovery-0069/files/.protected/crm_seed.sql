PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
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

INSERT INTO accounts (id, name, region, status, cancellation_reason) VALUES
    ('crm-169', 'Arbor Foods renewal', 'Mountain', 'active', NULL),
    ('crm-691', 'Arbor Foods renewal - distribution', 'Mountain', 'active', NULL),
    ('crm-240', 'Juniper Kitchens annual plan', 'Pacific', 'active', NULL);

INSERT INTO settings (name, value) VALUES
    ('first_cancel_timeout_consumed', '0');
