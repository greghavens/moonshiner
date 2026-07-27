PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('screening', 'interviewing', 'offer-review', 'closed')),
    role TEXT NOT NULL,
    owner TEXT NOT NULL
);

INSERT INTO candidates (id, name, location, status, role, owner) VALUES
    ('rec-271', 'Noelle Martin', 'Analytics', 'interviewing', 'Analytics Engineer', 'Mara Ellis'),
    ('rec-671', 'Ravi Patel', 'Customer Success', 'screening', 'Customer Success Partner', 'Jon Bell'),
    ('rec-1071', 'Noelle Martin archive', 'Design', 'closed', 'Design Operations Analyst', 'Mara Ellis');

CREATE TABLE mutation_receipts (
    receipt TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES candidates(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    created_ns INTEGER NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1))
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    record_id TEXT NOT NULL REFERENCES candidates(id),
    mutation_receipt TEXT NOT NULL UNIQUE REFERENCES mutation_receipts(receipt),
    created_ns INTEGER NOT NULL
);

CREATE TABLE audit_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    process_id INTEGER NOT NULL,
    parent_process_id INTEGER NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    result_count INTEGER,
    sole_id TEXT,
    before_status TEXT,
    after_status TEXT,
    receipt TEXT,
    recipient TEXT,
    message_sha256 TEXT,
    error TEXT,
    seal TEXT NOT NULL
);
