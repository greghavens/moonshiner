PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    stable_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    location TEXT NOT NULL,
    start_date TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived')),
    instructor TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    internal_note TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    session_date TEXT NOT NULL,
    open_seats INTEGER NOT NULL,
    PRIMARY KEY (location, session_date)
);

CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY,
    default_location TEXT NOT NULL,
    notification_channel TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES courses(stable_id),
    message TEXT NOT NULL
);

INSERT INTO courses (
    stable_id, title, status, location, start_date, lifecycle, instructor,
    capacity, internal_note
) VALUES
    (
        'edu-042', 'Foundations of Cartography', 'in-session', 'Online',
        '2026-07-06', 'current', 'Dr. Mira Alvarez', 36,
        'Standard eight-week section'
    ),
    (
        'edu-191', 'Urban Ecology Field Methods', 'waitlisted',
        'East Campus — Juniper Hall', '2026-09-02', 'current',
        'Prof. Theo Bennett', 24, 'Possible free-text collision'
    ),
    (
        'edu-192', 'Urban Ecology Field Methods', 'enrollment-open',
        'West Campus — Alder Hall', '2026-09-03', 'current',
        'Prof. Nia Okafor', 28, 'Stable ID is authoritative'
    ),
    (
        'edu-193', 'Urban Ecology Field Methods Lab', 'enrollment-open',
        'West Campus — Birch Annex', '2026-09-04', 'current',
        'Prof. Nia Okafor', 18, 'Related course, separate registration'
    ),
    (
        'edu-721', 'Community Planning Practicum', 'completed',
        'South Campus — Civic Studio', '2025-10-10', 'archived',
        'Dr. Salma Chen', 20, 'Historical course record'
    );

INSERT INTO availability (
    location, session_date, open_seats
) VALUES
    ('West Campus — Alder Hall', '2026-09-03', 7),
    ('West Campus — Birch Annex', '2026-09-04', 2),
    ('East Campus — Juniper Hall', '2026-09-02', 0);

INSERT INTO profiles (
    profile_id, default_location, notification_channel
) VALUES
    ('learner-primary', 'West Campus — Alder Hall', 'email');
