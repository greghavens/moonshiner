PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    application_date TEXT NOT NULL,
    status TEXT NOT NULL,
    recruiter TEXT NOT NULL,
    role_group TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO candidates
    (id, name, location, application_date, status, recruiter, role_group, notes)
VALUES
    ('cand_f3a91c72', 'Morgan Iqbal - Facilities Planner', 'Facilities',
     '2026-07-10', 'screening', 'Alex Romero', 'facilities-operations',
     'Candidate record prepared for recruiter handoff review.'),
    ('cand_e8d24b06', 'Riley Chen - Museum Educator', 'Education',
     '2026-07-12', 'interview', 'Sam Okafor', 'museum-education',
     'Interview stage confirmed for the current opening.'),
    ('cand_hist_16a4', 'Morgan Iqbal - Facilities Planner - 2025', 'Facilities',
     '2025-06-18', 'withdrawn', 'Alex Romero', 'facilities-operations',
     'Historical entry retained for prior hiring-cycle reporting.'),
    ('cand_hist_2bf9', 'Morgan Iqbal - Facilities Planner', 'Facilities Archive',
     '2025-04-03', 'archived', 'Taylor Singh', 'facilities-operations',
     'Historical alternate-location candidate record.'),
    ('cand_hist_73c1', 'Riley Chen - Museum Educator (Legacy)', 'Education',
     '2025-07-12', 'not-selected', 'Sam Okafor', 'museum-education',
     'Legacy candidate entry retained for reconciliation.'),
    ('cand_hist_84de', 'Riley Chen - Museum Educator', 'Education Archive',
     '2025-03-21', 'archived', 'Jordan Wells', 'museum-education',
     'Historical alternate-location candidate record.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('recruiter-handoff', 'candidate-summary');

CREATE TABLE availability (
    location TEXT NOT NULL,
    interview_date TEXT NOT NULL,
    interviewer_slots INTEGER NOT NULL,
    PRIMARY KEY (location, interview_date)
);

INSERT INTO availability (location, interview_date, interviewer_slots)
VALUES
    ('Facilities', '2026-07-27', 2),
    ('Education', '2026-07-28', 1);

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
