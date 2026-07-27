PRAGMA foreign_keys = ON;

CREATE TABLE archived_courses (
    id TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO archived_courses (id, location, name, status) VALUES
    ('edu-112', 'Downtown', 'Microeconomics', 'active'),
    ('edu-512', 'North Campus', 'Modern History', 'pending'),
    ('edu-912', 'North Campus', 'Microeconomics', 'closed');

CREATE TABLE operation_audit (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    output_path TEXT NOT NULL,
    result_count INTEGER,
    result_id TEXT,
    records_before_hash TEXT NOT NULL,
    records_after_hash TEXT,
    error TEXT
);
