PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    travel_date TEXT NOT NULL,
    status TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO trips
    (id, name, location, travel_date, status, coordinator, notes)
VALUES
    ('tra-272', 'Reykjavík Accessibility Workshop Trip', 'Reykjavík',
     '2026-11-12', 'planned', 'Nora Chen',
     'Accessibility review packet awaiting capacity confirmation.'),
    ('tra-672', 'Marrakesh Archive Consultation Trip', 'Marrakesh',
     '2026-11-12', 'planned', 'Nora Chen',
     'Archive consultation packet awaiting capacity confirmation.'),
    ('tra-872', 'Reykjavík Accessibility Workshop Trip',
     'Reykjavík Transit Desk', '2025-11-12', 'inactive', 'Eli Morgan',
     'Historical transit-desk itinerary retained for audit.'),
    ('tra-419', 'Marrakesh Conservation Planning Trip', 'Marrakesh',
     '2026-11-19', 'draft', 'Samira Vale',
     'Separate conservation planning itinerary.');

CREATE TABLE availability (
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    travel_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    remaining_capacity INTEGER NOT NULL CHECK (remaining_capacity >= 0),
    PRIMARY KEY (name, location, travel_date)
);

INSERT INTO availability
    (name, location, travel_date, available, remaining_capacity)
VALUES
    ('Reykjavík Accessibility Workshop Trip', 'Reykjavík',
     '2026-11-12', 1, 4),
    ('Marrakesh Archive Consultation Trip', 'Marrakesh',
     '2026-11-12', 0, 0),
    ('Reykjavík Accessibility Workshop Trip', 'Reykjavík Transit Desk',
     '2025-11-12', 0, 0),
    ('Marrakesh Conservation Planning Trip', 'Marrakesh',
     '2026-11-19', 1, 2);

CREATE TABLE saved_preferences (
    owner TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    PRIMARY KEY (owner, preference_key)
);

INSERT INTO saved_preferences (owner, preference_key, preference_value)
VALUES ('travel-coordinator', 'approval-window', 'three-business-days');

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    detail TEXT NOT NULL
);
