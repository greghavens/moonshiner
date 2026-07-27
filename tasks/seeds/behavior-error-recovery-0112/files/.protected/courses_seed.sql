PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    id TEXT PRIMARY KEY,
    course_name TEXT NOT NULL,
    campus TEXT NOT NULL,
    service_date TEXT NOT NULL,
    availability TEXT NOT NULL,
    seats_available INTEGER NOT NULL,
    capacity INTEGER NOT NULL,
    enrolled INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE(course_name, campus, service_date)
);

INSERT INTO courses
    (id, course_name, campus, service_date, availability,
     seats_available, capacity, enrolled, archived)
VALUES
    ('crs-eco112-dt', 'Microeconomics 112', 'Downtown', '2026-11-13',
     'available', 9, 28, 19, 0),
    ('crs-his112-nc', 'Modern History 112', 'North Campus', '2026-11-13',
     'full', 0, 24, 24, 0),
    ('crs-eco112-nc', 'Microeconomics 112', 'North Campus', '2026-11-13',
     'available', 3, 28, 25, 0),
    ('crs-eco112-dt14', 'Microeconomics 112', 'Downtown', '2026-11-14',
     'full', 0, 28, 28, 0),
    ('crs-his112-dt', 'Modern History 112', 'Downtown', '2026-11-13',
     'available', 4, 24, 20, 0),
    ('crs-his112-archive', 'Modern History 112 (Archived)', 'North Campus',
     '2025-11-13', 'closed', 0, 24, 24, 1);

CREATE TABLE transient_failures (
    course_name TEXT NOT NULL,
    campus TEXT NOT NULL,
    service_date TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY(course_name, campus, service_date)
);

INSERT INTO transient_failures
    (course_name, campus, service_date, remaining)
VALUES
    ('Modern History 112', 'North Campus', '2026-11-13', 1);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    outcome TEXT,
    error_code TEXT,
    retryable INTEGER,
    result_digest TEXT,
    seal TEXT
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    course_id TEXT,
    detail TEXT NOT NULL
);
