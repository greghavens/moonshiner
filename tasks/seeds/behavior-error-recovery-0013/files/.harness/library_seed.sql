PRAGMA foreign_keys = ON;

CREATE TABLE records (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    cancellation_reason TEXT
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (record_id) REFERENCES records(id)
);

CREATE TABLE service_state (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

INSERT INTO records (id, name, location, status, cancellation_reason) VALUES
    ('lib-113', 'River Almanac', 'Central', 'active', NULL),
    ('lib-513', 'Quiet Geometry', 'East', 'pending', NULL),
    ('lib-613', 'River Almanac', 'East', 'closed', NULL);

INSERT INTO service_state (key, value) VALUES ('cancel_calls', 0);
