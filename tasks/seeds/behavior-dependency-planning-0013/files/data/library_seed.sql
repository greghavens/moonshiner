PRAGMA foreign_keys = ON;

CREATE TABLE library_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    record_date TEXT NOT NULL,
    status TEXT NOT NULL,
    format TEXT NOT NULL,
    call_number TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE availability (
    stable_id TEXT PRIMARY KEY REFERENCES library_records(stable_id),
    copies_available INTEGER NOT NULL
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO library_records
    (stable_id, name, location, record_date, status, format, call_number, notes)
VALUES
    ('lib-113', 'The Riverkeeper''s Almanac', 'Central Branch',
     '2026-08-15', 'available', 'hardcover', 'NAT 333.91 RIV',
     'Current Central Branch circulation record.'),
    ('lib-513', 'Quiet Geometry', 'East Branch',
     '2026-08-16', 'on-hold', 'paperback', 'MAT 516.1 QUI',
     'Current East Branch circulation record.'),
    ('lib-913', 'The Riverkeeper''s Almanac', 'West Branch',
     '2025-08-15', 'withdrawn', 'hardcover', 'NAT 333.91 RIV',
     'Archived record at another branch.'),
    ('lib-813', 'Quiet Geometry', 'East Branch Storage',
     '2025-08-16', 'withdrawn', 'paperback', 'MAT 516.1 QUI',
     'Archived storage record with a similar location.'),
    ('lib-319', 'The Riverkeeper''s Field Notes', 'Central Branch',
     '2026-09-01', 'processing', 'hardcover', 'NAT 333.91 RIF',
     'Different title at the requested branch.');

INSERT INTO saved_preferences (preference_key, preference_value) VALUES
    ('default_branch', 'Central Branch'),
    ('result_sort', 'title');

INSERT INTO availability (stable_id, copies_available) VALUES
    ('lib-113', 1),
    ('lib-513', 0),
    ('lib-913', 0),
    ('lib-813', 0),
    ('lib-319', 0);
