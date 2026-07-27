PRAGMA foreign_keys = ON;

CREATE TABLE planning_items (
    id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL,
    program TEXT NOT NULL,
    proposed_review_date TEXT NOT NULL,
    status TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO planning_items
    (id, task_name, program, proposed_review_date, status, coordinator, notes)
VALUES
    ('pln-280', 'Emergency Signage Translation', 'Safety Program',
     '2026-12-02', 'awaiting-review-date', 'Avery Kim',
     'Translation package awaiting program-office scheduling.'),
    ('pln-681', 'Oral History Release Audit', 'Archive Program',
     '2026-12-02', 'awaiting-review-date', 'Avery Kim',
     'Release audit awaiting program-office scheduling.'),
    ('pln-882', 'Emergency Signage Translation', 'Facilities Program',
     '2026-12-09', 'draft', 'Jordan Bell',
     'Separate facilities translation review.'),
    ('pln-423', 'Oral History Rights Audit', 'Archive Program',
     '2026-12-16', 'scheduled', 'Morgan Lee',
     'Separate rights review retained for planning history.');

CREATE TABLE availability (
    task_name TEXT NOT NULL,
    program TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    reviewer_slots INTEGER NOT NULL CHECK (reviewer_slots >= 0),
    PRIMARY KEY (task_name, program, availability_date)
);

INSERT INTO availability
    (task_name, program, availability_date, available, reviewer_slots)
VALUES
    ('Emergency Signage Translation', 'Safety Program', '2026-12-02', 1, 2),
    ('Oral History Release Audit', 'Archive Program', '2026-12-02', 0, 0),
    ('Emergency Signage Translation', 'Facilities Program', '2026-12-09', 0, 0),
    ('Oral History Rights Audit', 'Archive Program', '2026-12-16', 1, 1);

CREATE TABLE saved_preferences (
    owner TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    PRIMARY KEY (owner, preference_key)
);

INSERT INTO saved_preferences (owner, preference_key, preference_value)
VALUES ('program-office', 'review-window', 'morning');

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    planning_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    planning_id TEXT NOT NULL,
    detail TEXT NOT NULL
);
