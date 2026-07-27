PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    record_date TEXT NOT NULL,
    status TEXT NOT NULL,
    recruiter TEXT NOT NULL,
    requisition TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO candidates
    (id, name, department, record_date, status, recruiter, requisition, notes)
VALUES
    ('rec-311', 'Morgan Iqbal — Fleet Analyst', 'Operations',
     '2026-09-17', 'reference-check', 'Avery Brooks', 'OPS-FA-26',
     'Reference review is in progress for the active fleet analyst opening.'),
    ('rec-711', 'Riley Chen — Grants Manager', 'Development',
     '2026-09-19', 'interview', 'Jordan Alvarez', 'DEV-GM-26',
     'Panel interview is scheduled for the active grants manager opening.'),
    ('rec-311-alt', 'Morgan Iqbal — Fleet Analyst duplicate', 'Archive',
     '2026-09-17', 'archived', 'Avery Brooks', 'OPS-FA-25',
     'Historical duplicate retained for records reconciliation.'),
    ('rec-311-hist', 'Morgan Iqbal — Fleet Analyst', 'Operations Archive',
     '2025-09-12', 'withdrawn', 'Casey Morgan', 'OPS-FA-25',
     'Historical same-name record in a different department.'),
    ('rec-711-hist', 'Riley Chen — Grants Manager (Legacy)', 'Development',
     '2025-08-08', 'not-selected', 'Jordan Alvarez', 'DEV-GM-25',
     'Historical similar-name record for an earlier opening.');

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
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
