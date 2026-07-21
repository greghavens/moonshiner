PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    travel_date TEXT NOT NULL
);

CREATE TABLE preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    travel_date TEXT NOT NULL,
    seats INTEGER NOT NULL,
    PRIMARY KEY (location, travel_date)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO trips (id, name, location, status, travel_date) VALUES
    ('tra-102', 'Kyoto Research Symposium Trip', 'Kyoto', 'booked', '2026-08-07'),
    ('tra-502', 'Montreal Supplier Site Visit', 'Montreal', 'awaiting-approval', '2026-08-08'),
    ('tra-902', 'Kyoto Research Symposium Trip', 'Kyoto, Minnesota', 'cancelled', '2025-08-07'),
    ('tra-802', 'Montreal Supplier Site Visit', 'Montreal Transit Desk', 'cancelled', '2025-08-08');

INSERT INTO preferences (preference_key, preference_value) VALUES
    ('cabin', 'economy'),
    ('meal', 'vegetarian');

INSERT INTO availability (location, travel_date, seats) VALUES
    ('Kyoto', '2026-08-07', 4),
    ('Montreal', '2026-08-08', 2);
