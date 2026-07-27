PRAGMA foreign_keys = ON;

CREATE TABLE operational_profile (
    profile_key TEXT PRIMARY KEY,
    default_date TEXT NOT NULL,
    preferred_quantity INTEGER NOT NULL CHECK (preferred_quantity > 0)
);

INSERT INTO operational_profile (profile_key, default_date, preferred_quantity)
VALUES ('saved', '2026-11-19', 1);

CREATE TABLE availability (
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (name, location, service_date)
);

INSERT INTO availability (name, location, service_date, available) VALUES
    ('Captioning request case', 'Cedar Clinic', '2026-11-19', 0),
    ('Mobile app login case', 'Delta Library', '2026-11-19', 1),
    ('Captioning request case', 'Cedar Clinic Annex', '2026-11-19', 1),
    ('Captioning request case', 'Cedar Clinic', '2026-11-20', 1),
    ('Mobile app login case', 'Delta Library', '2026-11-20', 0),
    ('Mobile app login case archive', 'Delta Library', '2026-11-19', 1);

CREATE TABLE support_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL
);

INSERT INTO support_records
    (stable_id, name, location, service_date, quantity, status, lifecycle)
VALUES
    ('sup-1045', 'Captioning request case archive', 'Acme Cooperative',
     '2025-09-08', 1, 'closed', 'archived');

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE registry_metadata (
    metadata_key TEXT PRIMARY KEY,
    metadata_value INTEGER NOT NULL
);

INSERT INTO registry_metadata (metadata_key, metadata_value)
VALUES ('next_record_number', 145);
