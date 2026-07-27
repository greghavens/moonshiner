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

CREATE TABLE requests (
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
    request_id TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO metadata(key, value) VALUES
    ('schema_version', '1'),
    ('created_record_id', 'facility-0137');

INSERT INTO profile(singleton, default_date, preferred_quantity)
VALUES (1, '2026-10-08', 1);

INSERT INTO availability(option_id, name, location, service_date, available) VALUES
    ('slot-137-a', 'Community hall chair setup', 'Civic Annex', '2026-10-08', 0),
    ('slot-137-b', 'East wing signage request', 'Operations Center', '2026-10-08', 1),
    ('slot-137-c', 'Community hall chair setup', 'Civic Depot', '2026-10-08', 1),
    ('slot-137-d', 'Community hall chair setup follow-up', 'Civic Annex', '2026-10-08', 1),
    ('slot-137-e', 'East wing signage request', 'Operations Center', '2026-10-09', 1);

INSERT INTO requests(id, name, location, service_date, quantity, status, archived, relation) VALUES
    ('facility-1137', 'Community hall chair setup archive', 'Civic Depot', '2025-10-08', 1, 'closed', 1, 'archived'),
    ('facility-2137', 'Community hall chair setup follow-up', 'Civic Annex', '2026-10-08', 1, 'pending', 0, 'related'),
    ('facility-3137', 'East wing signage request', 'Service Desk', '2026-10-08', 1, 'scheduled', 0, 'same-name');
