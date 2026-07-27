PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    trip_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    lifecycle TEXT NOT NULL CHECK (
        lifecycle IN ('current', 'cancelled', 'archived')
    )
);

CREATE TABLE availability_faults (
    stable_id TEXT PRIMARY KEY REFERENCES trips(stable_id),
    failures_remaining INTEGER NOT NULL CHECK (failures_remaining >= 0),
    attempts INTEGER NOT NULL CHECK (attempts >= 0)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES trips(stable_id),
    message TEXT NOT NULL
);

INSERT INTO trips
    (stable_id, name, location, trip_date, available, lifecycle)
VALUES
    ('tra-222', 'Kyoto visit 122', 'Kyoto', '2026-09-23', 1, 'current'),
    ('tra-622', 'Montreal visit 122', 'Montreal', '2026-09-23', 0, 'current'),
    ('tra-122', 'Kyoto visit 122 itinerary', 'Kyoto', '2026-09-23', 0, 'current'),
    ('tra-322', 'Kyoto visit 122', 'Osaka', '2026-09-23', 0, 'current'),
    ('tra-422', 'Kyoto visit 122', 'Kyoto', '2026-09-24', 0, 'current'),
    ('tra-522', 'Montreal visit 122 notes', 'Montreal', '2026-09-23', 1, 'current'),
    ('tra-722', 'Montreal visit 122', 'Quebec City', '2026-09-23', 1, 'current'),
    ('tra-822', 'Montreal visit 122', 'Montreal', '2026-09-23', 1, 'archived');

INSERT INTO availability_faults
    (stable_id, failures_remaining, attempts)
VALUES
    ('tra-222', 0, 0),
    ('tra-622', 1, 0);
