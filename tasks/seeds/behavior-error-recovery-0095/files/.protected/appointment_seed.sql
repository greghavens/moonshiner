PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    clinician TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancellation-pending', 'cancelled')
    ),
    cancellation_reason TEXT
);

CREATE TABLE settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE audit_events (
    sequence INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    seal TEXT NOT NULL
);

INSERT INTO appointments
    (id, name, clinician, scheduled_at, status, cancellation_reason)
VALUES
    ('hea-195', 'Vaccination visit — Priya Shah', 'Dr. Elena Ruiz',
     '2026-09-28T09:30:00-06:00', 'active', NULL),
    ('hea-915', 'Vaccination visit — Priya Shah (follow-up)', 'Dr. Elena Ruiz',
     '2026-10-12T09:30:00-06:00', 'active', NULL),
    ('hea-247', 'Annual wellness visit — Mateo Silva', 'Dr. Naomi Brooks',
     '2026-10-03T14:00:00-06:00', 'cancelled', 'patient rescheduled');

INSERT INTO settings (name, value) VALUES
    ('first_target_cancel_timeout_consumed', '0');
