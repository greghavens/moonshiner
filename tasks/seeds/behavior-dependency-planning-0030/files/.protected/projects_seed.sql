PRAGMA foreign_keys = ON;

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status_date TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO projects
    (id, name, location, status_date, status, owner, notes)
VALUES
    ('pro-130', 'Loading Dock Safety Audit', 'Facilities Program',
     '2026-08-16', 'review', 'Casey Morgan',
     'Current cross-team safety review.'),
    ('pro-530', 'Volunteer Handbook Refresh', 'Community Program',
     '2026-08-17', 'in-progress', 'Taylor Nguyen',
     'Current handbook refresh workstream.'),
    ('pro-930', 'Loading Dock Safety Audit', 'Facilities Backlog',
     '2025-08-16', 'completed', 'Casey Morgan',
     'Historical entry in an alternate program location.'),
    ('pro-731', 'Loading Dock Safety Audit - 2025', 'Facilities Program',
     '2025-08-16', 'completed', 'Drew Kim',
     'Prior-year named entry retained for reporting.'),
    ('pro-830', 'Volunteer Handbook Refresh', 'Community Program Backlog',
     '2025-08-17', 'completed', 'Taylor Nguyen',
     'Historical entry in an alternate program location.'),
    ('pro-642', 'Volunteer Handbook Refresh (Legacy)', 'Community Program',
     '2025-08-17', 'completed', 'Alex Rivera',
     'Legacy named entry retained for reporting.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('program-handoff', 'cross-team-status');

CREATE TABLE availability (
    location TEXT NOT NULL,
    status_date TEXT NOT NULL,
    reviewer_slots INTEGER NOT NULL,
    PRIMARY KEY (location, status_date)
);

INSERT INTO availability (location, status_date, reviewer_slots)
VALUES
    ('Facilities Program', '2026-08-16', 2),
    ('Community Program', '2026-08-17', 3);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    action_id TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
