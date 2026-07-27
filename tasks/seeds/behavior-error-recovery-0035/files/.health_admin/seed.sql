PRAGMA foreign_keys = ON;

CREATE TABLE appointments (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    cancellation_reason TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES appointments(stable_id),
    message TEXT NOT NULL
);

CREATE TABLE request_counters (
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    PRIMARY KEY (operation, stable_id)
);

INSERT INTO appointments
    (stable_id, name, location, status, cancellation_reason, revision)
VALUES
    ('hea-135', 'Dental cleaning', 'Cedar Clinic', 'active', NULL, 3),
    ('hea-535', 'Lab visit', 'Dale Clinic', 'pending', NULL, 2),
    ('hea-635', 'Dental cleaning', 'Dale Clinic', 'closed', NULL, 5);

INSERT INTO request_counters (operation, stable_id, attempts)
VALUES ('cancel', 'hea-135', 0);
