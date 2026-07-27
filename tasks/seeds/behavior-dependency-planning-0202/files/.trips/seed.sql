PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    trip_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES trips(stable_id),
    message TEXT NOT NULL
);

INSERT INTO trips
    (stable_id, name, location, status, trip_date, owner, lifecycle)
VALUES
    ('trip-302', 'Lisbon site visit', 'Lisbon', 'approved', '2026-09-10', 'Facilities review', 'current'),
    ('trip-702', 'Montréal policy summit', 'Montréal', 'awaiting-approval', '2026-09-18', 'Public policy', 'current'),
    ('trip-113', 'Lisbon site visit', 'Porto', 'rescheduled', '2026-09-12', 'Facilities review', 'current'),
    ('trip-224', 'Lisbon site visit debrief', 'Lisbon', 'draft', '2026-09-11', 'Facilities review', 'current'),
    ('trip-335', 'Lisbon site visit', 'Lisbon', 'cancelled', '2025-09-10', 'Facilities review', 'stale'),
    ('trip-446', 'Montreal policy summit', 'Montreal', 'approved', '2026-09-19', 'Public policy', 'current'),
    ('trip-557', 'Montréal policy summit', 'Québec City', 'confirmed', '2026-09-20', 'Public policy', 'current'),
    ('trip-668', 'Montréal policy summit briefing', 'Montréal', 'draft', '2026-09-17', 'Public policy', 'current'),
    ('trip-779', 'Montréal policy summit', 'Montréal', 'completed', '2025-09-18', 'Public policy', 'stale'),
    ('trip-880', 'Regional rail workshop', 'Brussels', 'confirmed', '2026-10-02', 'Mobility program', 'current');
