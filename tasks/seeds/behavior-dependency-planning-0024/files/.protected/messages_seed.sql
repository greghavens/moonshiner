PRAGMA foreign_keys = ON;

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    message_date TEXT NOT NULL,
    status TEXT NOT NULL,
    recipients TEXT NOT NULL,
    body TEXT NOT NULL
);

INSERT INTO messages
    (id, name, location, message_date, status, recipients, body)
VALUES
    ('msg_74d2a8', 'Storm Closure Advisory', 'Campus Operations',
     '2026-08-28', 'queued-for-review', 'Campus community',
     'Draft advisory for weather-related campus closure procedures.'),
    ('msg_b19ce4', 'Library Hours Correction', 'Student Services',
     '2026-08-29', 'approved', 'Students and faculty',
     'Correction to the library hours stated in the prior bulletin.'),
    ('msg_10aaf1', 'Storm Closure Advisory - 2025', 'Campus Operations',
     '2025-12-09', 'sent', 'Campus community',
     'Historical closure advisory retained for audit.'),
    ('msg_21bbf2', 'Storm Closure Advisory', 'Campus Operations Archive',
     '2025-11-18', 'archived', 'Campus community',
     'Historical alternate-location advisory.'),
    ('msg_32cc03', 'Library Hours Correction (2025)', 'Student Services',
     '2025-09-03', 'sent', 'Students and faculty',
     'Historical correction retained for audit.'),
    ('msg_43dd14', 'Library Hours Correction', 'Student Services Archive',
     '2025-08-27', 'canceled', 'Students and faculty',
     'Historical alternate-location correction.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('communications-lead', 'weekly-review');

CREATE TABLE availability (
    location TEXT NOT NULL,
    send_date TEXT NOT NULL,
    channel_available INTEGER NOT NULL,
    PRIMARY KEY (location, send_date)
);

INSERT INTO availability (location, send_date, channel_available)
VALUES
    ('Campus Operations', '2026-08-28', 1),
    ('Student Services', '2026-08-29', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
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
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
