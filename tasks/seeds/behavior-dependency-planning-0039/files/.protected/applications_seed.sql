PRAGMA foreign_keys = ON;

CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    submitted_on TEXT NOT NULL,
    status TEXT NOT NULL,
    permit_type TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO applications
    (id, name, location, submitted_on, status, permit_type, owner, notes)
VALUES
    ('pub-139', 'Sidewalk Cafe Permit Application', 'Denver',
     '2026-09-18', 'additional-info-needed', 'sidewalk-cafe', 'Avery Lane',
     'Current application in the municipal permit queue.'),
    ('pub-539', 'Residential Parking Permit Application', 'Boulder',
     '2026-09-19', 'approved', 'residential-parking', 'Morgan Reed',
     'Current residential parking application.'),
    ('pub-939', 'Sidewalk Cafe Permit Application', 'Denver County',
     '2025-09-18', 'expired', 'sidewalk-cafe', 'Avery Lane',
     'Historical application from a similarly named location.'),
    ('pub-839', 'Residential Parking Permit Application', 'Boulder County',
     '2025-09-19', 'expired', 'residential-parking', 'Morgan Reed',
     'Historical application from a similarly named location.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('permit-handoff', 'show-current-applications');

CREATE TABLE availability (
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    clerk_slots INTEGER NOT NULL,
    PRIMARY KEY (location, service_date)
);

INSERT INTO availability (location, service_date, clerk_slots)
VALUES
    ('Denver', '2026-09-22', 2),
    ('Boulder', '2026-09-22', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
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
