PRAGMA page_size = 4096;
PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'tentative', 'cancelled')),
    meeting_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE INDEX meetings_name_location ON meetings(name, location);

INSERT INTO meetings VALUES
    ('cal-101', 'Quarterly Roadmap Review', 'Denver HQ', 'confirmed', '2026-08-04', 'Regional PMO', 'Roadmap decisions and dependency review.'),
    ('cal-501', 'Budget Close Readout', 'Chicago Hub', 'tentative', '2026-08-05', 'Regional Finance', 'Close results pending final controller sign-off.'),
    ('cal-901', 'Quarterly Roadmap Review', 'Boulder Annex', 'cancelled', '2025-08-04', 'Legacy PMO', 'Archived prior-cycle event.'),
    ('cal-801', 'Budget Close Readout', 'Chicago Hub Archive', 'cancelled', '2025-08-05', 'Legacy Finance', 'Archived prior-cycle event.'),
    ('cal-330', 'Vendor Risk Review', 'Denver HQ', 'confirmed', '2026-08-06', 'Procurement', 'Quarterly vendor controls review.'),
    ('cal-640', 'Capacity Planning Forum', 'Chicago Hub', 'confirmed', '2026-08-07', 'Operations', 'Regional capacity discussion.');

CREATE TABLE saved_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO saved_preferences VALUES
    ('default_region', 'central'),
    ('time_format', '24h');

CREATE TABLE availability (
    location TEXT NOT NULL,
    slot TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (location, slot)
);

INSERT INTO availability VALUES
    ('Denver HQ', '2026-08-04T09:00:00-06:00', 0),
    ('Chicago Hub', '2026-08-05T14:00:00-05:00', 1);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

VACUUM;
