PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    id TEXT PRIMARY KEY,
    appointment TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    clinic TEXT NOT NULL,
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

INSERT INTO appointments (
    id, appointment, scheduled_for, clinic, status, cancellation_reason
) VALUES
    ('hea-175', 'Dental cleaning — Morgan Lee', '2026-08-14T09:30:00-06:00', 'North Dental Clinic', 'active', NULL),
    ('hea-715', 'Dental cleaning — Morgan Lee follow-up', '2026-08-28T09:30:00-06:00', 'North Dental Clinic', 'active', NULL),
    ('hea-246', 'Vision exam — Taylor Brooks', '2026-08-18T13:00:00-06:00', 'Central Eye Clinic', 'active', NULL);

INSERT INTO settings (name, value) VALUES
    ('first_cancel_timeout_consumed', '0');
