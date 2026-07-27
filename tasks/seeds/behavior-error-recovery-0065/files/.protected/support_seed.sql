PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    queue TEXT NOT NULL,
    status TEXT NOT NULL,
    cancellation_reason TEXT
);

INSERT INTO cases(id, title, queue, status, cancellation_reason) VALUES
    ('sup-165', 'Damaged exhibit shipment', 'Exhibitions Logistics', 'active', NULL),
    ('sup-615', 'Damaged exhibit shipment - carrier claim', 'Exhibitions Logistics', 'active', NULL);

CREATE TABLE settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO settings(name, value) VALUES
    ('first_cancel_timeout_consumed', '0');

CREATE TABLE audit_events (
    sequence INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    seal TEXT NOT NULL
);
