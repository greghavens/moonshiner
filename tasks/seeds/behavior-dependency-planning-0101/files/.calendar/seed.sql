PRAGMA foreign_keys = ON;

CREATE TABLE calendar_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    organizer TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES calendar_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO calendar_records
    (stable_id, name, location, status, organizer, starts_at, lifecycle)
VALUES
    ('cal-201', 'Vendor onboarding review', 'Denver HQ', 'confirmed', 'Vendor Operations', '2026-07-24T10:00:00-06:00', 'current'),
    ('cal-601', 'Quarterly finance check-in', 'Chicago Office', 'tentative', 'Corporate Finance', '2026-07-25T14:30:00-05:00', 'current'),
    ('cal-1001', 'Vendor onboarding review archive', 'Room Atlas', 'closed', 'Records Office', '2025-09-08T09:00:00-06:00', 'current'),
    ('cal-312', 'Vendor onboarding review', 'Chicago Office', 'tentative', 'Vendor Operations', '2026-07-29T11:00:00-05:00', 'current'),
    ('cal-423', 'Vendor onboarding reviews', 'Denver HQ', 'confirmed', 'Vendor Operations', '2026-07-30T10:00:00-06:00', 'current'),
    ('cal-534', 'Vendor onboarding review', 'Denver HQ', 'completed', 'Vendor Operations', '2025-07-24T10:00:00-06:00', 'archived'),
    ('cal-645', 'Quarterly finance check-ins', 'Chicago Office', 'confirmed', 'Corporate Finance', '2026-07-26T14:30:00-05:00', 'current'),
    ('cal-756', 'Quarterly finance check-in', 'Denver HQ', 'confirmed', 'Corporate Finance', '2026-07-28T13:30:00-06:00', 'current'),
    ('cal-867', 'Quarterly finance check-in prep', 'Chicago Office', 'draft', 'Corporate Finance', '2026-07-23T15:00:00-05:00', 'current'),
    ('cal-978', 'Quarterly finance check-in', 'Chicago Office', 'completed', 'Corporate Finance', '2025-07-25T14:30:00-05:00', 'archived'),
    ('cal-089', 'Operations weekly sync', 'Denver HQ', 'confirmed', 'Operations', '2026-07-22T09:00:00-06:00', 'current');
