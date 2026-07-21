PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    campus TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES courses(stable_id),
    message TEXT NOT NULL
);

INSERT INTO courses (stable_id, name, campus, status, lifecycle) VALUES
    ('edu-192', 'Environmental Economics', 'Downtown Campus', 'open', 'current'),
    ('edu-592', 'Oral History Workshop', 'North Campus', 'waitlisted', 'current'),
    ('edu-992', 'Environmental Economics archive', 'Riverside Annex', 'closed', 'current'),
    ('edu-284', 'Environmental Economics', 'West Campus', 'open', 'current'),
    ('edu-374', 'Environmental Economy', 'Downtown Campus', 'open', 'current'),
    ('edu-465', 'Environmental Economics', 'Downtown Campus', 'closed', 'stale'),
    ('edu-648', 'Oral Histories Workshop', 'North Campus', 'open', 'current'),
    ('edu-739', 'Oral History Workshop', 'South Campus', 'open', 'current'),
    ('edu-821', 'Oral History Workshop', 'North Campus', 'closed', 'stale'),
    ('edu-913', 'Urban Ecology Seminar', 'Downtown Campus', 'open', 'current');
