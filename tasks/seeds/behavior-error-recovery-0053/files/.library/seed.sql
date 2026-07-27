PRAGMA foreign_keys = ON;

CREATE TABLE records (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled', 'fulfilled')),
    cancellation_reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE faults (
    name TEXT PRIMARY KEY,
    remaining INTEGER NOT NULL CHECK (remaining >= 0)
);

CREATE TABLE invocations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    record_id TEXT,
    reason TEXT
);

CREATE TABLE operations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    record_id TEXT NOT NULL,
    reason TEXT,
    observed_status TEXT,
    resulting_status TEXT,
    outcome TEXT NOT NULL
);

INSERT INTO records (id, title, status, cancellation_reason, updated_at) VALUES
    ('lib-151', 'A Field Guide to Winter Light', 'active', NULL, '2032-04-08T09:30:00Z'),
    ('lib-152', 'Maps for Quiet Rivers', 'fulfilled', NULL, '2032-04-09T14:10:00Z'),
    ('lib-153', 'Orchards of the High Desert', 'active', NULL, '2032-04-10T11:45:00Z'),
    ('lib-154', 'The Small Museum Handbook', 'active', NULL, '2032-04-11T08:20:00Z');

INSERT INTO faults (name, remaining) VALUES
    ('lib-153:cancel-timeout-after-commit', 1);
