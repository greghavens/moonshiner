PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    vehicle_type TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'draft', 'archived'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    message TEXT NOT NULL
);

INSERT INTO vehicles
    (id, name, location, status, record_date, vehicle_type, coordinator, notes, lifecycle)
VALUES
    ('fle-340', 'Bus 14 museum charter', 'Depot E', 'reserved', '2026-10-25',
     'diesel bus', 'Morgan Lee', 'Museum entrance staging confirmed', 'current'),
    ('fle-740', 'EV 6 facilities inspection', 'Depot F', 'charging', '2026-10-26',
     'electric van', 'Riley Chen', 'Inspection equipment loaded', 'current'),
    ('fle-340-alt', 'Bus 14 museum charter quote', 'Planning', 'archived', '2026-10-25',
     'planning record', 'Taylor Singh', 'Superseded cost estimate', 'archived'),
    ('fle-340-draft', 'Bus 14 museum charter', 'Depot E', 'draft', '2026-10-28',
     'diesel bus', 'Morgan Lee', 'Unapproved duplicate draft', 'draft'),
    ('fle-740-draft', 'EV 6 facilities inspection draft', 'Depot F', 'draft', '2026-10-29',
     'electric van', 'Riley Chen', 'Unapproved follow-up draft', 'draft'),
    ('fle-902', 'Bus 14 school shuttle', 'Depot E', 'available', '2026-10-25',
     'diesel bus', 'Avery Patel', 'Regular weekday route', 'current');
