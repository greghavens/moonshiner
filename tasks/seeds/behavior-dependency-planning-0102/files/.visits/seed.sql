PRAGMA foreign_keys = ON;

CREATE TABLE visit_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    host TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES visit_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO visit_records
    (stable_id, name, location, status, host, starts_at, lifecycle)
VALUES
    ('visit-142', 'Kyoto archive visit', 'Kyoto', 'confirmed', 'Archive Services', '2026-08-07T10:00:00+09:00', 'current'),
    ('visit-731', 'Montreal partner visit', 'Montreal', 'tentative', 'Partner Relations', '2026-08-12T13:30:00-04:00', 'current'),
    ('visit-208', 'Kyoto archives visit', 'Kyoto', 'confirmed', 'Archive Services', '2026-08-08T10:00:00+09:00', 'current'),
    ('visit-319', 'Kyoto archive visit', 'Montreal', 'tentative', 'Archive Services', '2026-08-15T09:00:00-04:00', 'current'),
    ('visit-420', 'Kyoto archive visit planning', 'Kyoto', 'draft', 'Archive Services', '2026-08-05T14:00:00+09:00', 'current'),
    ('visit-531', 'Kyoto archive visit', 'Kyoto', 'completed', 'Archive Services', '2025-08-07T10:00:00+09:00', 'archived'),
    ('visit-642', 'Montreal partners visit', 'Montreal', 'confirmed', 'Partner Relations', '2026-08-13T13:30:00-04:00', 'current'),
    ('visit-753', 'Montreal partner visit', 'Kyoto', 'confirmed', 'Partner Relations', '2026-08-17T11:00:00+09:00', 'current'),
    ('visit-864', 'Montreal partner visit briefing', 'Montreal', 'draft', 'Partner Relations', '2026-08-10T15:00:00-04:00', 'current'),
    ('visit-975', 'Montreal partner visit', 'Montreal', 'completed', 'Partner Relations', '2025-08-12T13:30:00-04:00', 'archived'),
    ('visit-086', 'Collections team weekly', 'Kyoto', 'confirmed', 'Archive Services', '2026-08-06T09:00:00+09:00', 'current');
