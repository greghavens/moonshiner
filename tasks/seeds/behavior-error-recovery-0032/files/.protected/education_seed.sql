PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    id TEXT PRIMARY KEY,
    course_name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    department TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO courses
    (id, course_name, location, status, department, notes)
VALUES
    ('edu-112', 'Microeconomics', 'Downtown', 'active', 'Economics',
     'Downtown section scheduled for the autumn term.'),
    ('edu-512', 'Modern History', 'North Campus', 'pending', 'History',
     'North Campus section awaiting final room allocation.'),
    ('edu-612', 'Microeconomics', 'North Campus', 'pending', 'Economics',
     'Separate North Campus section retained for the registrar.'),
    ('edu-813', 'Modern Historiography', 'Downtown', 'active', 'History',
     'Separate graduate seminar retained for the registrar.');

CREATE TABLE availability (
    course_name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    seats_remaining INTEGER NOT NULL CHECK (seats_remaining >= 0),
    PRIMARY KEY (course_name, location, availability_date)
);

INSERT INTO availability
    (course_name, location, availability_date, available, seats_remaining)
VALUES
    ('Microeconomics', 'Downtown', '2026-11-23', 1, 4),
    ('Modern History', 'North Campus', '2026-11-23', 0, 0),
    ('Microeconomics', 'North Campus', '2026-11-23', 0, 0),
    ('Modern Historiography', 'Downtown', '2026-11-28', 1, 2);

CREATE TABLE saved_preferences (
    owner TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    PRIMARY KEY (owner, preference_key)
);

INSERT INTO saved_preferences (owner, preference_key, preference_value)
VALUES ('registrar-desk', 'display-window', 'compact');

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    course_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

