PRAGMA foreign_keys = ON;

CREATE TABLE applications (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    application_date TEXT NOT NULL,
    applicant TEXT NOT NULL,
    application_type TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES applications(stable_id),
    message TEXT NOT NULL
);

INSERT INTO applications
    (stable_id, name, location, status, application_date, applicant,
     application_type, lifecycle)
VALUES
    ('pub-299', 'Pet license renewal — Juniper', 'Aurora',
     'approved', '2026-08-27', 'Juniper Patel', 'pet-license', 'current'),
    ('pub-699', 'Community hall permit — spring fair', 'Lakewood',
     'cancellation-requested', '2026-08-29', 'Spring Fair Committee',
     'facility-permit', 'current'),
    ('pub-184', 'Pet license renewal — Juniper', 'Lakewood',
     'submitted', '2026-08-26', 'Juniper Patel', 'pet-license', 'current'),
    ('pub-299-alt', 'Pet license renewal — Juniper receipt', 'Aurora',
     'closed', '2026-08-27', 'Juniper Patel', 'payment-receipt', 'current'),
    ('pub-337', 'Pet license renewal - Juniper', 'Aurora',
     'denied', '2026-08-25', 'Juniper Patel', 'pet-license', 'current'),
    ('pub-448', 'Pet license renewal — Juniper', 'Aurora',
     'expired', '2025-08-27', 'Juniper Patel', 'pet-license', 'archived'),
    ('pub-572', 'Community hall permit — spring fair', 'Aurora',
     'approved', '2026-08-28', 'Spring Fair Committee', 'facility-permit',
     'current'),
    ('pub-731', 'Community hall permits — spring fair', 'Lakewood',
     'submitted', '2026-08-30', 'Spring Fair Committee', 'facility-permit',
     'current'),
    ('pub-845', 'Community hall permit — spring fair', 'Lakewood',
     'closed', '2025-08-29', 'Spring Fair Committee', 'facility-permit',
     'archived');
