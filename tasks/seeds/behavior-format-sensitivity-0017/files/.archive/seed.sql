PRAGMA foreign_keys = ON;

CREATE TABLE archived_requests (
    id TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO archived_requests (id, location, name, status) VALUES
    ('fac-117', 'Building A', 'Lighting repair', 'active'),
    ('fac-517', 'Building B', 'Room setup', 'pending'),
    ('fac-917', 'Building B', 'Lighting repair', 'closed');

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
