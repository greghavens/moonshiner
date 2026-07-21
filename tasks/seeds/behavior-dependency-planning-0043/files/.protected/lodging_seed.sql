PRAGMA foreign_keys = ON;

CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    arrival_date TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    room_count INTEGER NOT NULL,
    venue_note TEXT NOT NULL
);

INSERT INTO reservations
    (id, name, location, status, arrival_date, departure_date, room_count, venue_note)
VALUES
    ('lod_8ab31e', 'Northstar Speaker Lodging', 'Minneapolis', 'confirmed',
     '2026-10-08', '2026-10-11', 34, 'Hold referenced by the north venue note.'),
    ('lod_d47c92', 'Lakeside Volunteer Rooms', 'Madison', 'release-pending',
     '2026-10-15', '2026-10-18', 22, 'Hold referenced by the lakeside venue note.'),
    ('lod_125fa0', 'Northstar Speaker Lodging - 2025', 'Minneapolis', 'closed',
     '2025-10-09', '2025-10-12', 31, 'Historical block retained for audit.'),
    ('lod_26bc41', 'Northstar Speaker Lodging', 'Saint Paul', 'closed',
     '2025-11-06', '2025-11-09', 18, 'Historical alternate-location block.'),
    ('lod_37cd52', 'Lakeside Volunteer Rooms (2025)', 'Madison', 'closed',
     '2025-10-16', '2025-10-19', 20, 'Historical block retained for audit.'),
    ('lod_48de63', 'Lakeside Volunteer Rooms', 'Middleton', 'canceled',
     '2025-12-04', '2025-12-06', 14, 'Historical alternate-location block.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('events-lead', 'compact-view');

CREATE TABLE availability (
    location TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    rooms_available INTEGER NOT NULL,
    PRIMARY KEY (location, stay_date)
);

INSERT INTO availability (location, stay_date, rooms_available)
VALUES
    ('Minneapolis', '2026-10-08', 41),
    ('Madison', '2026-10-15', 27);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL,
    message TEXT NOT NULL
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
