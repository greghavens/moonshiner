PRAGMA foreign_keys = ON;

CREATE TABLE titles (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    collection TEXT NOT NULL,
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

INSERT INTO titles (id, title, collection, status, cancellation_reason) VALUES
    ('lib-173', 'River Almanac, fourth edition', 'Regional Reference', 'active', NULL),
    ('lib-713', 'River Almanac, fourth edition study guide', 'Regional Reference', 'active', NULL),
    ('lib-246', 'Woodland Weather Tables', 'Field Reference', 'active', NULL);

INSERT INTO settings (name, value) VALUES
    ('first_cancel_timeout_consumed', '0');
