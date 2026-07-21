PRAGMA foreign_keys = ON;

CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    status TEXT NOT NULL,
    property TEXT NOT NULL,
    rooms INTEGER NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    rooms_available INTEGER NOT NULL,
    PRIMARY KEY (location, stay_date)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO reservations
    (id, name, location, stay_date, status, property, rooms, notes)
VALUES
    ('lod-243', 'Orchard Room Leadership Retreat', 'Austin',
     '2026-10-12', 'contracted', 'Cedar House Austin', 14,
     'Leadership block held through the venue desk.'),
    ('lod-681', 'Juniper Suite Planning Stay', 'Raleigh',
     '2026-10-15', 'awaiting-deposit', 'Juniper Hotel Raleigh', 9,
     'Planning-team suite block awaiting deposit review.'),
    ('lod-812', 'Orchard Room Leadership Retreat', 'Austin, Minnesota',
     '2025-10-12', 'released', 'Prairie Orchard Inn', 6,
     'Archived retreat with a similar venue note.'),
    ('lod-934', 'Juniper Suite Planning Stay', 'Raleigh Annex',
     '2025-10-15', 'cancelled', 'Juniper Annex', 4,
     'Archived annex booking with a similar name.'),
    ('lod-417', 'Orchard Room Workshop', 'Austin',
     '2026-11-02', 'tentative', 'Cedar House Austin', 3,
     'Different event at the same property.');

INSERT INTO saved_preferences (preference_key, preference_value) VALUES
    ('bed_type', 'king'),
    ('billing', 'master-account');

INSERT INTO availability (location, stay_date, rooms_available) VALUES
    ('Austin', '2026-10-12', 18),
    ('Raleigh', '2026-10-15', 11);
