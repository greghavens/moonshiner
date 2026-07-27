PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    cancellation_reason TEXT
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
);

CREATE TABLE service_state (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

INSERT INTO candidates (id, name, location, status, cancellation_reason) VALUES
    ('rec-111', 'Casey Evans', 'Sales', 'active', NULL),
    ('rec-511', 'Devon Flores', 'Research', 'pending', NULL),
    ('rec-611', 'Casey Evans', 'Research', 'closed', NULL);

INSERT INTO service_state (key, value) VALUES ('cancel_calls', 0);
