PRAGMA journal_mode = DELETE;
PRAGMA synchronous = FULL;

CREATE TABLE titles (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    branch TEXT NOT NULL,
    hold_until TEXT,
    status TEXT NOT NULL
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE audit (
    seq INTEGER PRIMARY KEY,
    time_ns INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    ppid INTEGER NOT NULL,
    operation TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    previous_digest TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE
);

INSERT INTO titles (id, title, branch, hold_until, status) VALUES
    ('TTL-6609', 'Orchard Birds', 'North Branch', NULL, 'active'),
    ('TTL-6610', 'Orchard Birds', 'South Branch', '2026-08-21', 'on-hold'),
    ('TTL-6611', 'Orchard Birds: Field Notes', 'North Branch', '2026-09-04', 'available'),
    ('TTL-9609', 'Lanterns at Noon', 'West Branch', '2026-09-18', 'pending');
