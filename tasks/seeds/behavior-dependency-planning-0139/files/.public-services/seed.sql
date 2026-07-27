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
    option_id TEXT NOT NULL,
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
    ('created_record_id', 'pub-c139');

INSERT INTO profile(singleton, default_date, preferred_quantity)
VALUES (1, '2026-11-13', 1);

INSERT INTO availability(option_id, name, location, service_date, available) VALUES
    ('permit-139-a', 'Food cart license', 'Arvada', '2026-11-13', 0),
    ('permit-139-b', 'Block party application', 'Wheat Ridge', '2026-11-13', 1),
    ('permit-139-c', 'Food cart license', 'Wheat Ridge', '2026-11-13', 1),
    ('permit-139-d', 'Food cart licensing', 'Arvada', '2026-11-13', 1),
    ('permit-139-e', 'Block party application', 'Arvada', '2026-11-13', 1),
    ('permit-139-f', 'Block party application', 'Wheat Ridge', '2026-11-12', 1),
    ('permit-139-g', 'Block party permit', 'Wheat Ridge', '2026-11-13', 1);

INSERT INTO requests(
    id, option_id, name, location, service_date, quantity, status, archived, relation
) VALUES
    ('pub-1039', 'archive-139-a', 'Food cart license archive', 'Aurora', '2026-02-01', 1, 'closed', 1, 'archived'),
    ('pub-2039', 'permit-139-e', 'Block party application', 'Arvada', '2026-11-13', 2, 'pending', 0, 'other-location'),
    ('pub-3039', 'permit-139-f', 'Block party application', 'Wheat Ridge', '2026-11-12', 1, 'submitted', 0, 'other-date');

INSERT INTO notifications(request_id, message)
VALUES ('pub-2039', 'Existing municipal review note');
