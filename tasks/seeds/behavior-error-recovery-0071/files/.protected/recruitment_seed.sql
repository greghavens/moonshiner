PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hiring_team TEXT NOT NULL,
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

INSERT INTO candidates (id, name, hiring_team, status, cancellation_reason) VALUES
    ('rec-171', 'Casey Evans — Data Steward', 'Data Governance', 'active', NULL),
    ('rec-711', 'Casey Evans — Data Stewardship Analyst', 'Data Governance', 'active', NULL),
    ('rec-244', 'Morgan Lee — Records Coordinator', 'Operations', 'active', NULL);

INSERT INTO settings (name, value) VALUES
    ('first_cancel_timeout_consumed', '0');
