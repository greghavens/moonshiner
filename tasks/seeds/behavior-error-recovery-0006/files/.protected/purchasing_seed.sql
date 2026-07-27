PRAGMA foreign_keys = ON;

CREATE TABLE requisitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    status TEXT NOT NULL,
    requester TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO requisitions
    (id, name, location, requested_date, status, requester, notes)
VALUES
    ('pur-276', 'Braille Labeler Order', 'Accessibility Office',
     '2026-11-21', 'pending-release', 'Mina Patel',
     'Accessibility equipment requisition awaiting fulfillment check.'),
    ('pur-676', 'Portable Freezer Order', 'Field Lab',
     '2026-11-21', 'pending-release', 'Mina Patel',
     'Cold-chain equipment requisition awaiting fulfillment check.'),
    ('pur-876', 'Braille Labeler Order', 'Accessibility Storage',
     '2025-11-21', 'received', 'Owen Brooks',
     'Historical storage order retained for purchasing audit.'),
    ('pur-419', 'Portable Incubator Order', 'Field Lab',
     '2026-11-28', 'draft', 'Lena Ortiz',
     'Separate laboratory equipment request.');

CREATE TABLE availability (
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    units_available INTEGER NOT NULL CHECK (units_available >= 0),
    PRIMARY KEY (name, location, availability_date)
);

INSERT INTO availability
    (name, location, availability_date, available, units_available)
VALUES
    ('Braille Labeler Order', 'Accessibility Office', '2026-11-21', 1, 6),
    ('Portable Freezer Order', 'Field Lab', '2026-11-21', 0, 0),
    ('Braille Labeler Order', 'Accessibility Storage', '2025-11-21', 0, 0),
    ('Portable Incubator Order', 'Field Lab', '2026-11-28', 1, 3);

CREATE TABLE saved_preferences (
    owner TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    PRIMARY KEY (owner, preference_key)
);

INSERT INTO saved_preferences (owner, preference_key, preference_value)
VALUES ('purchasing-desk', 'release-window', 'two-business-days');

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    requisition_id TEXT NOT NULL,
    detail TEXT NOT NULL
);
