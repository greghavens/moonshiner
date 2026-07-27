PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    id TEXT PRIMARY KEY,
    traveler_name TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_at TEXT NOT NULL,
    arrival_at TEXT NOT NULL,
    status TEXT NOT NULL,
    carrier TEXT NOT NULL,
    confirmation_code TEXT NOT NULL
);

INSERT INTO trips
    (
        id,
        traveler_name,
        origin,
        destination,
        departure_at,
        arrival_at,
        status,
        carrier,
        confirmation_code
    )
VALUES
    (
        'tra-142',
        'Morgan Patel',
        'Denver Union Station',
        'Glenwood Springs',
        '2026-08-14T07:10:00-06:00',
        '2026-08-14T12:46:00-06:00',
        'confirmed',
        'Mountain Rail',
        'MR8K2Q'
    ),
    (
        'tra-542',
        'Casey Nguyen',
        'Denver International Airport',
        'Seattle-Tacoma International Airport',
        '2026-08-22T14:35:00-06:00',
        '2026-08-22T16:42:00-07:00',
        'queued',
        'Cascadia Air',
        'CA4H7P'
    ),
    (
        'tra-124',
        'Avery Jones',
        'Colorado Springs',
        'Albuquerque',
        '2026-09-03T09:20:00-06:00',
        '2026-09-03T10:32:00-06:00',
        'pending',
        'Mesa Airlink',
        'MA1N6D'
    ),
    (
        'tra-142-archive',
        'Morgan Patel',
        'Denver Union Station',
        'Winter Park',
        '2024-01-19T06:45:00-07:00',
        '2024-01-19T09:07:00-07:00',
        'completed',
        'Mountain Rail',
        'MR3T9W'
    );
