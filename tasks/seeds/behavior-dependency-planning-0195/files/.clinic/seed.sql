PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    stable_id TEXT PRIMARY KEY,
    appointment TEXT NOT NULL,
    clinic TEXT NOT NULL,
    status TEXT NOT NULL,
    appointment_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    room TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES appointments(stable_id),
    message TEXT NOT NULL
);

INSERT INTO appointments
    (stable_id, appointment, clinic, status, appointment_date,
     provider, room, lifecycle)
VALUES
    ('apt-51c92fa7', 'Dental cleaning — Morgan Lee', 'Cedar Clinic',
     'confirmed', '2026-09-03', 'Dr. Priya Shah', 'Cedar 4', 'current'),
    ('apt-8e34d1b6', 'Lab follow-up — Avery Chen', 'Dale Clinic',
     'completed', '2026-09-05', 'Nurse Mateo Ruiz', 'Dale Lab 2', 'current'),
    ('apt-106', 'Dental cleaning — Morgan Lee', 'Birch Clinic',
     'scheduled', '2026-09-04', 'Dr. Elise Ward', 'Birch 3', 'current'),
    ('apt-207', 'Dental cleaning - Morgan Lee', 'Cedar Clinic',
     'waitlisted', '2026-09-06', 'Dr. Priya Shah', 'Cedar 5', 'current'),
    ('apt-308', 'Dental cleaning — Morgan Lee', 'Cedar Clinic',
     'cancelled', '2025-09-03', 'Dr. Priya Shah', 'Cedar 4', 'archived'),
    ('apt-409', 'Lab follow-up — Avery Chen', 'Elm Clinic',
     'scheduled', '2026-09-07', 'Nurse Tara Bell', 'Elm Lab 1', 'current'),
    ('apt-510', 'Lab follow up — Avery Chen', 'Dale Clinic',
     'pending', '2026-09-08', 'Nurse Mateo Ruiz', 'Dale Lab 3', 'current'),
    ('apt-611', 'Lab follow-up — Avery Chen', 'Dale Clinic',
     'completed', '2025-09-05', 'Nurse Mateo Ruiz', 'Dale Lab 2', 'archived'),
    ('apt-712', 'Dental consultation — Morgan Lee', 'Cedar Clinic',
     'confirmed', '2026-09-09', 'Dr. Priya Shah', 'Cedar 4', 'current');

INSERT INTO notifications (stable_id, message)
VALUES ('apt-409', 'Bring prior lab paperwork');
