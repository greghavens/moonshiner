PRAGMA foreign_keys = ON;

CREATE TABLE hospitality_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    guest_contact TEXT NOT NULL,
    reserved_for TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES hospitality_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO hospitality_records
    (stable_id, name, location, status, guest_contact, reserved_for, lifecycle)
VALUES
    ('hos-203', 'Maple Hall reception', 'Austin', 'confirmed', 'Events Desk', '2026-08-14T18:00:00-05:00', 'current'),
    ('hos-603', 'Orchid Suite lodging', 'Raleigh', 'held', 'Guest Services', '2026-08-14T15:00:00-04:00', 'current'),
    ('hos-1003', 'Maple Hall reception archive', 'Portland', 'closed', 'Records Office', '2025-08-14T18:00:00-07:00', 'archived'),
    ('hos-314', 'Orchid Suite lodging', 'Austin', 'held', 'Guest Services', '2026-08-18T15:00:00-05:00', 'current'),
    ('hos-425', 'Orchid Suites lodging', 'Raleigh', 'waitlisted', 'Guest Services', '2026-08-16T15:00:00-04:00', 'current'),
    ('hos-536', 'Orchid Suite lodging', 'Raleigh', 'completed', 'Guest Services', '2025-08-14T15:00:00-04:00', 'archived'),
    ('hos-647', 'Maple Hall receptions', 'Austin', 'confirmed', 'Events Desk', '2026-08-15T18:00:00-05:00', 'current'),
    ('hos-758', 'Maple Hall reception', 'Raleigh', 'tentative', 'Events Desk', '2026-08-19T18:00:00-04:00', 'current'),
    ('hos-869', 'Maple Hall reception', 'Austin', 'completed', 'Events Desk', '2025-08-14T18:00:00-05:00', 'archived'),
    ('hos-071', 'Harbor Lounge reception', 'Austin', 'confirmed', 'Events Desk', '2026-08-17T19:00:00-05:00', 'current');
