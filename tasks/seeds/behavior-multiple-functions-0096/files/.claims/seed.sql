PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = ON;

CREATE TABLE claims (
    position INTEGER NOT NULL UNIQUE,
    id TEXT PRIMARY KEY,
    claimant TEXT NOT NULL,
    type TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    filed_date TEXT NOT NULL
);

CREATE TABLE profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    date TEXT NOT NULL,
    open_slots INTEGER NOT NULL CHECK (open_slots >= 0)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    message TEXT NOT NULL
);

INSERT INTO claims (
    position, id, claimant, type, location, status, filed_date
) VALUES
    (1, 'clm-804', 'Jordan Patel', 'Property', 'North Office', 'active', '2026-02-14'),
    (2, 'clm-217', 'Renee Cho', 'Auto', 'North Office', 'pending', '2026-03-09'),
    (3, 'clm-563', 'Amari Johnson', 'Workers compensation', 'North Office', 'active', '2026-01-28'),
    (4, 'clm-091', 'Luis Ortega', 'Property', 'South Office', 'active', '2026-02-19'),
    (5, 'clm-742', 'Mina Hassan', 'Auto', 'North Office', 'active', '2026-03-02'),
    (6, 'clm-338', 'North Office Holdings', 'Liability', 'West Office', 'active', '2026-02-05'),
    (7, 'clm-429', 'Evelyn Brooks', 'Property', 'North Office Annex', 'active', '2026-01-17'),
    (8, 'clm-615', 'Sofia Kim', 'Auto', 'North Office', 'closed', '2025-12-22');

INSERT INTO profile (key, value) VALUES
    ('default_office', 'Central Office'),
    ('saved_view', 'recently-filed');

INSERT INTO availability (location, date, open_slots) VALUES
    ('North Office', '2026-04-06', 3),
    ('South Office', '2026-04-06', 1);
