PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    course_date TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO courses (id, name, location, status, course_date, coordinator, notes)
VALUES
    ('crs-214', 'Nonprofit Budgeting Workshop', 'Civic Center',
     'registration-open', '2026-09-14', 'Community Finance Office',
     'Materials are prepared for the fall cohort.'),
    ('crs-731', 'Oral History Methods', 'Library Annex',
     'waitlist', '2026-10-03', 'Local History Desk',
     'The public session has reached its room limit.'),
    ('crs-318', 'Nonprofit Budgeting Workshop', 'Northside Center',
     'completed', '2026-04-12', 'Neighborhood Grants Office',
     'This was a separate spring workshop.'),
    ('crs-842', 'Oral History Methods', 'Central Library',
     'registration-open', '2026-11-08', 'Archives Education',
     'This is the main-library offering.'),
    ('crs-455', 'Nonprofit Finance Lab', 'Civic Center',
     'scheduled', '2026-09-21', 'Community Finance Office',
     'The lab is distinct from the budgeting workshop.'),
    ('crs-966', 'Community Interview Basics', 'Library Annex',
     'cancelled', '2026-08-29', 'Local History Desk',
     'This is not the oral-history methods course.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('enrollment-desk', 'compact-course-view');

CREATE TABLE availability (
    location TEXT NOT NULL,
    course_date TEXT NOT NULL,
    seats INTEGER NOT NULL,
    PRIMARY KEY (location, course_date)
);

INSERT INTO availability (location, course_date, seats)
VALUES
    ('Civic Center', '2026-09-14', 8),
    ('Library Annex', '2026-10-03', 0);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    course_id TEXT NOT NULL,
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

