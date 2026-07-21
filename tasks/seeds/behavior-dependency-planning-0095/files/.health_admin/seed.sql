PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES appointments(stable_id),
    message TEXT NOT NULL
);

INSERT INTO appointments (stable_id, name, location, status, lifecycle)
VALUES
    ('hea-195', 'Dental cleaning — Morgan Lee', 'Cedar Clinic', 'confirmed', 'current'),
    ('hea-595', 'Lab visit — Avery Shah', 'Dale Clinic', 'requested', 'current'),
    ('hea-995', 'Dental cleaning — Morgan Lee archive', 'Northside Center', 'closed', 'archived'),
    ('hea-206', 'Dental cleaning — Morgan Lee', 'Dale Clinic', 'scheduled', 'current'),
    ('hea-317', 'Dental cleaning — Morgan Leigh', 'Cedar Clinic', 'requested', 'current'),
    ('hea-428', 'Dental cleaning — Morgan Lee', 'Cedar Clinic', 'cancelled', 'cancelled'),
    ('hea-609', 'Lab visit — Avery Shah', 'Cedar Clinic', 'requested', 'current'),
    ('hea-720', 'Lab follow-up — Avery Shah', 'Dale Clinic', 'requested', 'current'),
    ('hea-831', 'Lab visit — Avery Shaw', 'Dale Clinic', 'needs-review', 'current'),
    ('hea-942', 'Lab visit — Avery Shah', 'Dale Clinic', 'closed', 'archived');
