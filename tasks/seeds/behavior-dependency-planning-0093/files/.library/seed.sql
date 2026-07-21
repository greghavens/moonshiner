PRAGMA foreign_keys = ON;

CREATE TABLE titles (
    stable_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    branch TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES titles(stable_id),
    message TEXT NOT NULL
);

INSERT INTO titles (stable_id, title, branch, status, lifecycle) VALUES
    ('lib-193', 'Tidepool Field Guide', 'Central Branch', 'available', 'current'),
    ('lib-593', 'The Cartographer''s Lantern', 'East Branch', 'on-loan', 'current'),
    ('lib-993', 'Tidepool Field Guide archive', 'West Branch', 'reference-only', 'current'),
    ('lib-285', 'Tidepool Field Guide', 'North Branch', 'available', 'current'),
    ('lib-375', 'Tide Pool Field Guide', 'Central Branch', 'on-loan', 'current'),
    ('lib-466', 'Tidepool Field Guide', 'Central Branch', 'withdrawn', 'stale'),
    ('lib-649', 'The Cartographers Lantern', 'East Branch', 'available', 'current'),
    ('lib-730', 'The Cartographer''s Lantern', 'South Branch', 'available', 'current'),
    ('lib-822', 'The Cartographer''s Lantern', 'East Branch', 'available', 'stale'),
    ('lib-914', 'Coastal Navigation Atlas', 'Central Branch', 'available', 'current');
