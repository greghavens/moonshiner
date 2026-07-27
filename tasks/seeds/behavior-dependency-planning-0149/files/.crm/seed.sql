PRAGMA foreign_keys = ON;

CREATE TABLE operational_profile (
    profile_key TEXT PRIMARY KEY,
    default_date TEXT NOT NULL,
    preferred_quantity INTEGER NOT NULL CHECK (preferred_quantity > 0)
);

CREATE TABLE availability (
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (name, location, service_date)
);

CREATE TABLE crm_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE registry_metadata (
    metadata_key TEXT PRIMARY KEY,
    metadata_value INTEGER NOT NULL
);

INSERT INTO operational_profile
    (profile_key, default_date, preferred_quantity)
VALUES
    ('saved', '2026-11-23', 1);

INSERT INTO availability
    (name, location, service_date, available)
VALUES
    ('Bluebird Literacy Project', 'Northeast Region', '2026-11-23', 0),
    ('Bluebird Literacy Project', 'Northeast Region', '2026-11-24', 1),
    ('Bluebird Literacy Project', 'Northwest Region', '2026-11-23', 1),
    ('Mosaic Bicycle Works', 'South Region', '2026-11-23', 1),
    ('Mosaic Bicycle Works', 'South Region', '2026-11-24', 0),
    ('Mosaic Bicycle Works archive', 'South Region', '2026-11-23', 1);

INSERT INTO crm_records
    (stable_id, name, location, service_date, quantity, status, lifecycle)
VALUES
    ('crm-1049', 'Bluebird Literacy Project archive', 'West Region',
     '2026-10-12', 1, 'closed', 'archived'),
    ('crm-1128', 'Regional Arts Collaborative', 'Central Region',
     '2026-11-18', 2, 'prospect', 'current');

INSERT INTO registry_metadata (metadata_key, metadata_value)
VALUES ('next_record_number', 149);
