PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    ship_date TEXT NOT NULL,
    status TEXT NOT NULL,
    carrier TEXT NOT NULL,
    handling_units INTEGER NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO shipments
    (id, name, location, ship_date, status, carrier, handling_units, notes)
VALUES
    ('shp_128c7a41', 'Orchard Exhibit Crate', 'Austin Depot',
     '2026-08-10', 'customs-review', 'Lone Star Freight', 1,
     'Exhibit crate held for customs documentation review.'),
    ('shp_528e9d63', 'Juniper Poster Tube', 'Raleigh Depot',
     '2026-08-11', 'in-transit', 'Piedmont Parcel', 3,
     'Poster tubes departed the regional sorting hub.'),
    ('shp_928b0f15', 'Orchard Exhibit Crate - 2025', 'Austin Depot',
     '2025-08-10', 'delivered', 'Lone Star Freight', 1,
     'Historical exhibit shipment retained for reconciliation.'),
    ('shp_2ad683e0', 'Orchard Exhibit Crate', 'Austin Events Store',
     '2025-07-29', 'archived', 'Lone Star Freight', 1,
     'Historical alternate-location record.'),
    ('shp_828f341c', 'Juniper Poster Tube (2025)', 'Raleigh Depot',
     '2025-08-11', 'delivered', 'Piedmont Parcel', 2,
     'Prior-year poster shipment retained for reconciliation.'),
    ('shp_4ec9207b', 'Juniper Poster Tube', 'Raleigh Depot Returns',
     '2025-06-10', 'returned', 'Piedmont Parcel', 2,
     'Historical alternate-location record.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('logistics-handoff', 'exception-board');

CREATE TABLE availability (
    location TEXT NOT NULL,
    ship_date TEXT NOT NULL,
    dock_capacity INTEGER NOT NULL,
    PRIMARY KEY (location, ship_date)
);

INSERT INTO availability (location, ship_date, dock_capacity)
VALUES
    ('Austin Depot', '2026-08-10', 2),
    ('Raleigh Depot', '2026-08-11', 4);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    action_id TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
