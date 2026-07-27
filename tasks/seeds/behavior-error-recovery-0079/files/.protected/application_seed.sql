PRAGMA foreign_keys = ON;

CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
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

INSERT INTO applications (id, name, department, status, cancellation_reason) VALUES
    ('pub-179', 'Pet license renewal — Juniper', 'Animal Services', 'active', NULL),
    ('pub-719', 'Pet license renewal — Juniper II', 'Animal Services', 'active', NULL),
    ('pub-246', 'Block party permit — Juniper Street', 'Public Events',
     'cancellation-pending', 'awaiting applicant confirmation');

INSERT INTO settings (name, value) VALUES
    ('first_cancel_timeout_consumed', '0');
