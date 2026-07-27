PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    trip_date TEXT NOT NULL,
    availability TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived'))
);

CREATE TABLE transient_plan (
    stable_id TEXT PRIMARY KEY REFERENCES trips(stable_id),
    remaining_failures INTEGER NOT NULL CHECK (remaining_failures >= 0)
);

INSERT INTO trips
    (stable_id, name, location, trip_date, availability, lifecycle)
VALUES
    ('trip-827', 'Lisbon site visit', 'Lisbon', '2026-08-27',
     'available', 'current'),
    ('trip-627', 'Montréal policy summit', 'Montréal', '2026-08-27',
     'waitlist-only', 'current'),
    ('trip-927', 'Lisbon site visit', 'Porto', '2026-08-27',
     'unavailable', 'current'),
    ('trip-527', 'Lisbon site visits', 'Lisbon', '2026-08-27',
     'limited', 'current'),
    ('trip-427', 'Lisbon site visit', 'Lisbon', '2026-08-26',
     'unavailable', 'current'),
    ('trip-327', 'Montréal policy summit', 'Toronto', '2026-08-27',
     'available', 'current'),
    ('trip-227', 'Montreal policy summit', 'Montréal', '2026-08-27',
     'available', 'current'),
    ('trip-127', 'Montréal policy summit', 'Montréal', '2025-08-27',
     'unavailable', 'archived'),
    ('trip-727', 'Regional planning retreat', 'Québec City', '2026-08-28',
     'limited', 'current');

INSERT INTO transient_plan (stable_id, remaining_failures)
VALUES
    ('trip-827', 0),
    ('trip-627', 1),
    ('trip-727', 2);
