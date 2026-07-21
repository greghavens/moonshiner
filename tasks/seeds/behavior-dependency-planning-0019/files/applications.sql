PRAGMA foreign_keys = ON;

CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    date TEXT NOT NULL
);

INSERT INTO applications (id, name, location, status, date) VALUES
    ('pub-119', 'Pet License Renewal Application', 'Aurora', 'submitted', '2026-08-08'),
    ('pub-519', 'Community Room Permit Application', 'Lakewood', 'under-review', '2026-08-09'),
    ('pub-919', 'Pet License Renewal Application', 'Aurora County', 'expired', '2025-08-08'),
    ('pub-819', 'Community Room Permit Application', 'Lakewood County', 'expired', '2025-08-09');

CREATE TABLE saved_preferences (
    clerk TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO saved_preferences (clerk, value) VALUES
    ('intake', 'compact');

CREATE TABLE availability (
    location TEXT NOT NULL,
    day TEXT NOT NULL,
    slots INTEGER NOT NULL,
    PRIMARY KEY (location, day)
);

INSERT INTO availability (location, day, slots) VALUES
    ('Aurora', '2026-08-08', 4),
    ('Lakewood', '2026-08-09', 2);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE audit_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
