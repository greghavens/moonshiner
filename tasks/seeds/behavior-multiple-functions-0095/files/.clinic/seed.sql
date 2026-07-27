PRAGMA foreign_keys = ON;

CREATE TABLE services (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived')),
    internal_code TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

CREATE TABLE availability (
    stable_id TEXT NOT NULL REFERENCES services(stable_id),
    service_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL CHECK (open_slots >= 0),
    first_open_time TEXT,
    PRIMARY KEY (stable_id, service_date)
);

CREATE TABLE clinic_profiles (
    location TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    timezone TEXT NOT NULL,
    portal_label TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES services(stable_id),
    message TEXT NOT NULL
);

INSERT INTO services (
    stable_id, name, location, duration_minutes, status, lifecycle,
    internal_code, internal_note
) VALUES
    (
        'svc-114', 'Annual wellness visit', 'Dale Clinic', 60, 'active',
        'current', 'AWV-DAL-60', 'Preventive visit scheduling template'
    ),
    (
        'svc-127', 'Annual wellness visit', 'Pine Clinic', 60, 'active',
        'current', 'AWV-PIN-60', 'Different clinic'
    ),
    (
        'svc-203', 'Annual wellness intake', 'Dale Clinic', 30, 'active',
        'current', 'AWI-DAL-30', 'Different service name'
    ),
    (
        'svc-318', 'Follow-up visit', 'Dale Clinic', 30, 'active',
        'current', 'FUV-DAL-30', 'General follow-up'
    ),
    (
        'svc-444', 'Annual wellness visit', 'Lake Clinic', 60, 'active',
        'current', 'AWV-LAK-60', 'Different clinic'
    ),
    (
        'svc-509', 'Annual wellness visit', 'Dale Clinic', 45, 'retired',
        'archived', 'AWV-DAL-45-OLD', 'Archived template'
    );

INSERT INTO availability (
    stable_id, service_date, open_slots, first_open_time
) VALUES
    ('svc-114', '2026-11-13', 1, '14:10'),
    ('svc-114', '2026-11-20', 3, '09:20'),
    ('svc-114', '2026-11-27', 0, NULL),
    ('svc-127', '2026-11-20', 4, '10:00'),
    ('svc-203', '2026-11-20', 2, '08:40'),
    ('svc-318', '2026-11-20', 5, '11:30'),
    ('svc-444', '2026-11-20', 1, '15:00'),
    ('svc-509', '2026-11-20', 6, '07:45');

INSERT INTO clinic_profiles (
    location, phone, timezone, portal_label
) VALUES
    ('Dale Clinic', '555-0104', 'America/Denver', 'Dale patient portal'),
    ('Pine Clinic', '555-0119', 'America/Denver', 'Pine patient portal'),
    ('Lake Clinic', '555-0162', 'America/Denver', 'Lake patient portal');
