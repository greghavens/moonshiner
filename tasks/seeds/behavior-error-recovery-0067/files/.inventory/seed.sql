PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE items (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancellation-pending', 'cancelled', 'fulfilled')
    ),
    cancellation_reason TEXT,
    cancellation_requests INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requests >= 0)
);

CREATE TABLE service_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO items (
    stable_id, name, status, cancellation_reason, cancellation_requests
) VALUES
    ('inv-167', 'Acid-free archive folders', 'active', NULL, 0),
    ('inv-176', 'Acid-free archival folders', 'active', NULL, 0),
    ('inv-204', 'Buffered document boxes', 'cancelled', 'replaced by reusable totes', 1),
    ('inv-311', 'Oversize map sleeves', 'fulfilled', NULL, 0);

INSERT INTO service_metadata (key, value) VALUES
    ('delay_first_committed_cancel', '1');
