PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    interview_date TEXT,
    status TEXT,
    recruiter TEXT NOT NULL,
    application_source TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    message TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    finished_ns INTEGER
);

INSERT INTO candidates
    (id, name, department, interview_date, status, recruiter,
     application_source, notes)
VALUES
    ('cand-2f91c8', 'Morgan Shah — facilities planner',
     'Workplace Strategy', '2026-08-12', 'interviewing',
     'Evelyn Brooks', 'internal-referral',
     'Portfolio emphasizes workplace utilization studies.'),
    ('cand-7a42e1', 'Morgan Shah — facilities planner',
     'Operations', NULL, 'screening',
     'Diego Alvarez', 'careers-site',
     'Interview date has not been entered.'),
    ('cand-64c0b3', 'Morgan Shah — senior facilities planner',
     'Operations', '2026-08-18', 'panel-review',
     'Diego Alvarez', 'agency',
     'Senior-role applicant; not the facilities planner opening.'),
    ('cand-90d7aa', 'Morgan Shah — facilities planner',
     'People Operations', '2026-08-21', 'interviewing',
     'Priya Raman', 'careers-site',
     'People Operations workforce-planning role.'),
    ('cand-185ed4', 'Morgana Shah — facilities planner',
     'Operations', '2026-08-25', 'new',
     'Diego Alvarez', 'event',
     'Different candidate with a similar name.'),
    ('cand-b031f6', 'Morgan Shah — facilities planning analyst',
     'Operations', '2026-08-16', 'withdrawn',
     'Diego Alvarez', 'careers-site',
     'Archived analyst application.');
