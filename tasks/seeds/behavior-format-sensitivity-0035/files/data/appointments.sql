PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    id TEXT PRIMARY KEY,
    patient_name TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    timezone TEXT NOT NULL,
    clinician TEXT NOT NULL,
    visit_type TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO appointments
    (id, patient_name, starts_at, timezone, clinician, visit_type, status)
VALUES
    ('hea-135', 'Mara Ellison', '2026-08-04T09:30:00', 'America/Denver', 'Dr. Imani Cole', 'medication_review', 'confirmed'),
    ('hea-535', 'Jonah Patel', '2026-08-04T11:00:00', 'America/Denver', 'Dr. Lena Ortiz', 'annual_wellness', 'confirmed'),
    ('hea-315', 'Rina Okafor', '2026-08-05T14:15:00', 'America/Denver', 'Dr. Imani Cole', 'follow_up', 'pending'),
    ('hea-135-archive', 'Mara Ellison', '2025-08-06T09:30:00', 'America/Denver', 'Dr. Imani Cole', 'medication_review', 'completed');
