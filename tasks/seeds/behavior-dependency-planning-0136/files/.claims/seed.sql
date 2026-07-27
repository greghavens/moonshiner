PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE profile (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    default_date TEXT NOT NULL,
    preferred_quantity INTEGER NOT NULL CHECK (preferred_quantity > 0)
);

CREATE TABLE availability (
    option_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    UNIQUE (name, location, service_date)
);

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL,
    archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
    relation TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO metadata(key, value) VALUES
    ('schema_version', '1'),
    ('created_claim_id', 'ins-c136');

INSERT INTO profile(singleton, default_date, preferred_quantity)
VALUES (1, '2026-11-10', 1);

INSERT INTO availability(option_id, name, location, service_date, available) VALUES
    ('slot-136-a', 'Roof hail claim', 'Central Office', '2026-11-10', 0),
    ('slot-136-b', 'Rental car damage claim', 'Online Intake', '2026-11-10', 1),
    ('slot-136-c', 'Roof hail claim', 'West Office', '2026-11-10', 1),
    ('slot-136-d', 'Roof hail claim follow-up', 'Central Office', '2026-11-10', 1),
    ('slot-136-e', 'Rental car damage claim', 'Online Intake', '2026-11-11', 1);

INSERT INTO claims(id, name, location, service_date, quantity, status, archived, relation) VALUES
    ('ins-1036', 'Roof hail claim archive', 'West Office', '2025-11-10', 1, 'closed', 1, 'archived'),
    ('ins-2036', 'Roof hail claim follow-up', 'Central Office', '2026-11-10', 1, 'pending', 0, 'related'),
    ('ins-3036', 'Rental car damage claim', 'Telephone Intake', '2026-11-10', 1, 'under-review', 0, 'same-name');
