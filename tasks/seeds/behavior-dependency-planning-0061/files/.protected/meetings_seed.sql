PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE UNIQUE INDEX meetings_name_location_id
    ON meetings(name, location, stable_id);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO meetings
    (stable_id, name, location, status, meeting_date, owner, notes)
VALUES
    ('meet_a94c7e12', 'Museum Partnership Call', 'Portland Studio',
     'confirmed', '2026-10-09', 'Regional Partnerships',
     'Video link is held in the internal calendar.'),
    ('meet_5bd281f0', 'Grant Committee Work Session', 'Atlanta Office',
     'pending organizer confirmation', '2026-10-10', 'Grants Office',
     'Room assignment is held in the internal calendar.'),
    ('meet_e2037bc6', 'Museum Partnership Call', 'Portland Warehouse',
     'cancelled', '2025-10-09', 'Regional Partnerships',
     'Historical record retained for audit.'),
    ('meet_70f4a1d9', 'Grant Committee Work Session', 'Atlanta Office Archive',
     'cancelled', '2025-10-10', 'Grants Office',
     'Historical record retained for audit.'),
    ('meet_c61f8e34', 'Museum Partnership Planning Call', 'Portland Studio',
     'completed', '2026-08-22', 'Regional Partnerships',
     'Separate planning meeting.'),
    ('meet_19d3b6a8', 'Grant Committee Work Session', 'Atlanta Annex',
     'completed', '2026-07-14', 'Grants Office',
     'Separate committee meeting.');

INSERT INTO saved_preferences (preference_key, preference_value)
VALUES
    ('default_view', 'regional-week'),
    ('include_cancelled', 'false');

PRAGMA user_version = 1;
