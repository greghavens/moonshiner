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
    ('lod_7f3a91', 'Foundry Hall Reception', 'Pittsburgh', 'confirmed',
     '2026-09-17', '2026-09-19', 28, 'Hold referenced by the foundry venue note.'),
    ('lod_c842de', 'Glasshouse Dinner Reservation', 'Cleveland', 'release-pending',
     '2026-09-24', '2026-09-26', 19, 'Hold referenced by the glasshouse venue note.'),
    ('lod_135ab8', 'Foundry Hall Reception - 2025', 'Pittsburgh', 'closed',
     '2025-09-18', '2025-09-20', 24, 'Historical block retained for audit.'),
    ('lod_24c9f1', 'Foundry Hall Reception', 'Erie', 'canceled',
     '2025-11-05', '2025-11-07', 16, 'Historical alternate-location block.'),
    ('lod_3d651a', 'Glasshouse Dinner Reservation (2025)', 'Cleveland', 'closed',
     '2025-09-25', '2025-09-27', 17, 'Historical block retained for audit.'),
    ('lod_4eb207', 'Glasshouse Dinner Reservation', 'Akron', 'canceled',
     '2025-12-03', '2025-12-05', 12, 'Historical alternate-location block.');

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
    ('Pittsburgh', '2026-09-17', 36),
    ('Cleveland', '2026-09-24', 23);

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
