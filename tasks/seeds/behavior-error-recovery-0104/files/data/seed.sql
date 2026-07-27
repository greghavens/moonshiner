PRAGMA page_size = 4096;
PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = ON;

CREATE TABLE campaigns (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    campaign_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'pending', 'inactive')),
    available INTEGER NOT NULL CHECK (available IN (0, 1))
);

CREATE UNIQUE INDEX campaigns_exact_record
    ON campaigns(name, location, campaign_date);

INSERT INTO campaigns VALUES
    ('mes-204', 'Fleet maintenance alert', 'Drivers', '2026-10-08', 'queued', 1),
    ('mes-604', 'Benefits deadline reminder', 'Employees', '2026-10-08', 'pending', 1),
    ('mes-204-alt', 'Fleet maintenance alert sample', 'Managers', '2026-10-08', 'inactive', 0),
    ('mes-205', 'Fleet maintenance alert', 'Drivers', '2026-10-09', 'inactive', 0),
    ('mes-605', 'Benefits deadline reminder', 'Contractors', '2026-10-08', 'inactive', 0),
    ('mes-880', 'Annual enrollment announcement', 'Employees', '2026-10-08', 'queued', 1);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

VACUUM;
