PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    patient_name TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancelled', 'completed', 'no_show')
    ),
    cancellation_reason TEXT,
    version INTEGER NOT NULL,
    cancellation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id TEXT NOT NULL REFERENCES appointments(id),
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE service_control (
    appointment_id TEXT NOT NULL REFERENCES appointments(id),
    fault TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY (appointment_id, fault)
);

INSERT INTO appointments (
    id, title, patient_name, scheduled_for, location, status,
    cancellation_reason, version, cancellation_count
) VALUES
    (
        'hea-155', 'Dental cleaning', 'Morgan Bailey',
        '2026-08-14T09:30:00-06:00', 'Cedar Clinic', 'active', NULL, 6, 0
    ),
    (
        'hea-515', 'Dental cleaning', 'Morgan Bailey',
        '2026-08-14T09:30:00-06:00', 'Cedar Clinic', 'active', NULL, 4, 0
    ),
    (
        'hea-255', 'Dental X-ray', 'Alexis Reed',
        '2026-08-15T11:00:00-06:00', 'North Clinic', 'active', NULL, 2, 0
    ),
    (
        'hea-355', 'Dental cleaning', 'Jamie Lin',
        '2026-07-02T14:00:00-06:00', 'Cedar Clinic', 'completed', NULL, 8, 0
    );

INSERT INTO service_control (appointment_id, fault, remaining)
VALUES ('hea-155', 'cancel_timeout_without_commit', 1);
