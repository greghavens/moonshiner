PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    course_date TEXT NOT NULL,
    status TEXT NOT NULL,
    registrar_contact TEXT NOT NULL,
    subject_area TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO courses
    (id, name, location, course_date, status, registrar_contact, subject_area, notes)
VALUES
    ('edu-132', 'Community Soil Science', 'West Campus',
     '2026-09-24', 'enrollment-open', 'Marisol Vega', 'environmental-studies',
     'Current catalog entry prepared for enrollment desk reconciliation.'),
    ('edu-532', 'Introduction to Museum Lighting', 'Arts Campus',
     '2026-09-22', 'registration-closed', 'Devon Shah', 'museum-studies',
     'Current catalog entry reviewed for the upcoming term.'),
    ('edu-932', 'Community Soil Science', 'West Campus Online',
     '2025-09-22', 'archived', 'Marisol Vega', 'environmental-studies',
     'Historical online entry retained for prior-year reporting.'),
    ('edu-731', 'Community Soil Science - 2025', 'West Campus',
     '2025-09-20', 'archived', 'Lee Foster', 'environmental-studies',
     'Historical similarly named on-campus entry.'),
    ('edu-832', 'Introduction to Museum Lighting', 'Arts Campus Online',
     '2025-09-23', 'archived', 'Devon Shah', 'museum-studies',
     'Historical online entry retained for reconciliation.'),
    ('edu-641', 'Introduction to Museum Lighting (Historical)', 'Arts Campus',
     '2025-09-21', 'archived', 'Avery Brown', 'museum-studies',
     'Historical similarly named arts-campus entry.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('enrollment-desk', 'date-ordered-reconciliation');

CREATE TABLE availability (
    location TEXT NOT NULL,
    session_date TEXT NOT NULL,
    seats_available INTEGER NOT NULL,
    PRIMARY KEY (location, session_date)
);

INSERT INTO availability (location, session_date, seats_available)
VALUES
    ('West Campus', '2026-09-24', 8),
    ('Arts Campus', '2026-09-22', 0);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
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
