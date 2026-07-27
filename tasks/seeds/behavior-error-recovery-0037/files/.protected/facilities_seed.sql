PRAGMA foreign_keys = ON;

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    requested_for TEXT NOT NULL,
    status TEXT NOT NULL,
    requester TEXT NOT NULL,
    notes TEXT NOT NULL,
    cancellation_reason TEXT
);

INSERT INTO requests
    (id, name, location, requested_for, status, requester, notes,
     cancellation_reason)
VALUES
    ('fac-137', 'Lighting repair', 'Building A', '2026-08-14', 'active',
     'Operations Desk', 'Scheduled electrical work awaiting dispatch.', NULL),
    ('fac-537', 'Room setup', 'Building B', '2026-08-15', 'pending',
     'Events Desk', 'Conference room setup request.', NULL),
    ('fac-637', 'Lighting repair', 'Building B', '2026-07-30', 'closed',
     'Operations Desk', 'Historical lighting request completed in July.', NULL);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE fault_plan (
    request_id TEXT PRIMARY KEY,
    lost_response_remaining INTEGER NOT NULL
);

INSERT INTO fault_plan (request_id, lost_response_remaining)
VALUES ('fac-137', 1);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    committed INTEGER NOT NULL DEFAULT 0,
    result_digest TEXT,
    error_kind TEXT
);
