PRAGMA foreign_keys = ON;

CREATE TABLE facilities_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    site TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES facilities_records(stable_id)
);

INSERT INTO facilities_records
    (stable_id, name, site, status, lifecycle)
VALUES
    ('fac-257', 'Archive humidity check', 'Preservation Annex', 'assigned', 'current'),
    ('fac-657', 'Rooftop garden access request', 'Central Office', 'queued', 'current'),
    ('fac-1257', 'Archive humidity check follow-up', 'Records Center', 'queued', 'current'),
    ('fac-1657', 'Rooftop garden access request', 'Former Headquarters', 'closed', 'archived');

INSERT INTO notifications (stable_id, message)
VALUES ('fac-1257', 'Existing facilities reminder');
