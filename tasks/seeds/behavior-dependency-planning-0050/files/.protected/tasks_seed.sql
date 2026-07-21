PRAGMA foreign_keys = ON;

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO tasks (id, name, location, status, record_date, owner, notes)
VALUES
    ('tsk-150', 'Donor Data Cleanup', 'Advancement Program', 'in-progress',
     '2026-07-18', 'Data Stewardship', 'Duplicate-review queue is being reconciled.'),
    ('tsk-650', 'Exhibit Label Proofing', 'Museum Program', 'awaiting-approval',
     '2026-07-19', 'Interpretation Team', 'Final bilingual label set is with curatorial review.'),
    ('tsk-350', 'Donor Data Cleanup', 'Annual Giving Program', 'completed',
     '2026-06-03', 'Annual Giving', 'Separate campaign-data cleanup.'),
    ('tsk-450', 'Exhibit Label Proofing', 'Museum Annex', 'blocked',
     '2026-06-04', 'Annex Exhibitions', 'Annex labels are a separate installation.'),
    ('tsk-550', 'Donor Import Cleanup', 'Advancement Program', 'not-started',
     '2026-08-01', 'Data Operations', 'Import mapping is not donor data cleanup.'),
    ('tsk-750', 'Exhibit Label Printing', 'Museum Program', 'scheduled',
     '2026-08-02', 'Exhibit Production', 'Printing begins after proof approval.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('program-office', 'compact-cross-team-view');

CREATE TABLE availability (
    location TEXT NOT NULL,
    workstream TEXT NOT NULL,
    available_reviewers INTEGER NOT NULL,
    PRIMARY KEY (location, workstream)
);

INSERT INTO availability (location, workstream, available_reviewers)
VALUES
    ('Advancement Program', 'data-quality', 2),
    ('Museum Program', 'exhibit-production', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    task_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
