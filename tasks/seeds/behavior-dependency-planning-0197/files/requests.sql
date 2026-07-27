PRAGMA foreign_keys = ON;

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    building TEXT NOT NULL,
    request_date TEXT NOT NULL,
    status TEXT NOT NULL,
    requester TEXT NOT NULL,
    details TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
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
    parent_executable TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    error TEXT,
    violation INTEGER NOT NULL DEFAULT 0
);

INSERT INTO requests
    (id, name, building, request_date, status, requester, details)
VALUES
    (
        'req_41c8fa72',
        'East stairwell lighting repair',
        'Building A',
        '2026-08-04',
        'scheduled',
        'Mara Ortiz',
        'Replace failed fixtures on landings two and three.'
    ),
    (
        'req_b93d20e6',
        'Training room setup',
        'Building B',
        '2026-08-01',
        'ready',
        'Dylan Cho',
        'Arrange tables, projector, and twelve training stations.'
    ),
    (
        'req_hist_0d31',
        'East stairwell lighting repair',
        'Building A Archive',
        '2025-08-04',
        'completed',
        'Mara Ortiz',
        'Archived request from the prior annual maintenance cycle.'
    ),
    (
        'req_hist_28aa',
        'East stairwell lighting repair - 2025',
        'Building A',
        '2025-06-19',
        'completed',
        'Facilities Desk',
        'Historical title retained for audit purposes.'
    ),
    (
        'req_hist_771e',
        'Training room setup',
        'Building B Archive',
        '2025-08-01',
        'completed',
        'Dylan Cho',
        'Archived setup request from the previous training series.'
    ),
    (
        'req_hist_c240',
        'Training room setup - Q2',
        'Building B',
        '2026-05-11',
        'completed',
        'Learning Services',
        'Earlier quarterly setup request retained for audit purposes.'
    );

PRAGMA user_version = 1;
