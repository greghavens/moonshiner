PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    appointment_date TEXT NOT NULL,
    clinician TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    appointment_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (location, appointment_date)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    appointment_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    parent_started_ticks INTEGER NOT NULL,
    violation INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT
);

INSERT INTO appointments
    (id, name, location, status, appointment_date, clinician, notes)
VALUES
    ('appt-271', 'Annual Vision Screening', 'Maple Clinic', 'confirmed', '2026-08-14', 'Dr. Leena Shah', 'Bring current eyewear and insurance card.'),
    ('appt-319', 'Annual Vision Screening', 'Cedar Clinic', 'checked-in', '2026-08-10', 'Dr. Owen Reed', 'A different clinic appointment with the same name.'),
    ('appt-448', 'Retinal Imaging Follow-up', 'Maple Clinic', 'scheduled', '2026-08-18', 'Dr. Leena Shah', 'Imaging follow-up for another patient.'),
    ('appt-864', 'Vaccination Review', 'Orchard Clinic', 'pending-intake', '2026-08-16', 'Nurse Pilar Gomez', 'Review immunization records at check-in.'),
    ('appt-892', 'Vaccination Review', 'River Clinic', 'confirmed', '2026-08-19', 'Nurse Mina Cole', 'A different clinic appointment with the same name.'),
    ('appt-935', 'Medication Consultation', 'Orchard Clinic', 'scheduled', '2026-08-21', 'Dr. Aaron Bell', 'Medication list review for another patient.');

INSERT INTO saved_preferences (owner, preference) VALUES
    ('clinic-scheduler', 'front-desk-summary'),
    ('report-owner', 'status-only');

INSERT INTO availability (location, appointment_date, open_slots) VALUES
    ('Maple Clinic', '2026-08-14', 3),
    ('Orchard Clinic', '2026-08-16', 1);
