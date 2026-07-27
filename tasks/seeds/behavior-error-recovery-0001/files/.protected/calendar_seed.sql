PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL,
    organizer TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO meetings
    (id, title, location, starts_at, status, organizer, notes)
VALUES
    ('cal-271', 'Mountain Region Staffing Sync', 'Denver Operations Center',
     '2026-08-06T15:00:00-06:00', 'scheduled', 'Avery Lin',
     'Duplicate entry pending removal before invitation reconciliation.'),
    ('cal-244', 'Mountain Region Staffing Sync', 'Denver Operations Center',
     '2026-08-06T15:00:00-06:00', 'scheduled', 'Avery Lin',
     'Canonical staffing meeting retained for invitation reconciliation.'),
    ('cal-318', 'Mountain Region Staffing Sync', 'Boulder Annex',
     '2026-08-07T10:30:00-06:00', 'scheduled', 'Morgan Tate',
     'Separate Boulder Annex staffing meeting.'),
    ('cal-105', 'Front Range Recruiting Review', 'Denver Operations Center',
     '2026-08-05T13:00:00-06:00', 'scheduled', 'Jordan Bell',
     'Unrelated recruiting review.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('meeting-administrator', 'week-start=monday');

CREATE TABLE availability (
    location TEXT NOT NULL,
    calendar_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (location, calendar_date)
);

INSERT INTO availability (location, calendar_date, open_slots)
VALUES
    ('Denver Operations Center', '2026-08-06', 2),
    ('Boulder Annex', '2026-08-07', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    committed INTEGER NOT NULL DEFAULT 0,
    result_digest TEXT,
    error_kind TEXT
);
