PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    appointment_date TEXT,
    status TEXT,
    clinician TEXT NOT NULL,
    room TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO appointments
    (id, name, location, appointment_date, status, clinician, room, notes)
VALUES
    ('appt-731', 'Nutrition Counseling Visit', 'Harbor Clinic',
     '2026-08-04', 'confirmed', 'Dr. Mina Iqbal', 'H-204',
     'Current nutrition appointment for the front-desk handoff.'),
    ('appt-284', 'Physical Therapy Intake', 'Juniper Clinic',
     NULL, 'awaiting-forms', 'Evan Cho, DPT', 'J-112',
     'Intake date has not yet been entered in the appointment record.'),
    ('appt-119', 'Nutrition Counseling Visit', 'Harbor Clinic East',
     '2025-07-16', 'completed', 'Dr. Mina Iqbal', 'E-108',
     'Historical appointment at a different clinic location.'),
    ('appt-447', 'Nutrition Counseling Visit (2025)', 'Harbor Clinic',
     '2025-07-18', 'completed', 'Dr. Adele Park', 'H-201',
     'Historical similarly named appointment.'),
    ('appt-508', 'Physical Therapy Intake', 'Juniper Rehabilitation',
     '2025-06-09', 'completed', 'Evan Cho, DPT', 'R-014',
     'Historical intake at a different location.'),
    ('appt-906', 'Physical Therapy Intake - Archive', 'Juniper Clinic',
     '2025-06-11', 'canceled', 'Lena Moss, DPT', 'J-109',
     'Historical similarly named appointment retained for audit.'),
    ('appt-335', 'Postoperative Mobility Review', 'Juniper Clinic',
     '2026-08-05', 'confirmed', 'Lena Moss, DPT', 'J-107',
     'Unrelated current appointment.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('front-desk-handoff', 'show-next-business-day');

CREATE TABLE availability (
    location TEXT NOT NULL,
    appointment_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (location, appointment_date)
);

INSERT INTO availability (location, appointment_date, open_slots)
VALUES
    ('Harbor Clinic', '2026-08-04', 2),
    ('Juniper Clinic', '2026-08-05', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    action_id TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
