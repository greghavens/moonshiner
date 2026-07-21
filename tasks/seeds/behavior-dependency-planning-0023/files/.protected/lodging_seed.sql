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
    ('lod_7f31b2', 'Harborview Workshop Block', 'Baltimore', 'confirmed',
     '2026-09-14', '2026-09-17', 28, 'Hold referenced by the waterfront venue note.'),
    ('lod_c84e19', 'Cedar Court Guest Block', 'Richmond', 'release-pending',
     '2026-09-21', '2026-09-23', 19, 'Hold referenced by the courtyard venue note.'),
    ('lod_10ac55', 'Harborview Workshop Block - 2025', 'Baltimore', 'closed',
     '2025-09-15', '2025-09-18', 24, 'Historical block retained for audit.'),
    ('lod_21bd66', 'Harborview Workshop Block', 'Baltimore Harbor', 'closed',
     '2025-10-03', '2025-10-05', 16, 'Historical alternate-location block.'),
    ('lod_32ce77', 'Cedar Court Guest Block (2025)', 'Richmond', 'closed',
     '2025-09-22', '2025-09-24', 17, 'Historical block retained for audit.'),
    ('lod_43df88', 'Cedar Court Guest Block', 'Richmond Annex', 'canceled',
     '2025-11-08', '2025-11-10', 12, 'Historical annex block retained for audit.');

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
    ('Baltimore', '2026-09-14', 34),
    ('Richmond', '2026-09-21', 22);

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
