PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE clinic_records (
    stable_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    patient TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES clinic_records(stable_id)
);

INSERT INTO clinic_records
    (stable_id, title, patient, status, scheduled_for, lifecycle)
VALUES
    ('hea-104', 'Nutrition consultation', 'Jordan Lee', 'completed',
     '2026-07-19T09:00:00-06:00', 'current'),
    ('hea-155', 'Physical therapy', 'Robin Stone', 'confirmed',
     '2026-07-22T11:00:00-06:00', 'current'),
    ('hea-255', 'Physical therapy', 'Alex Green', 'confirmed',
     '2026-07-22T14:30:00-06:00', 'current'),
    ('hea-518', 'Annual wellness visit', 'Morgan Reed', 'confirmed',
     '2026-07-24T10:00:00-06:00', 'current'),
    ('hea-655', 'Imaging appointment', 'Casey Bell', 'requested',
     '2026-07-23T08:15:00-06:00', 'current'),
    ('hea-656', 'Imaging appointment', 'Casey Bell', 'cancelled',
     '2026-06-18T08:15:00-06:00', 'archived');

INSERT INTO notifications (stable_id, channel, message)
VALUES ('hea-104', 'email', 'Visit summary is ready.');
