PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    ship_date TEXT NOT NULL,
    status TEXT NOT NULL,
    carrier TEXT NOT NULL,
    service_level TEXT NOT NULL,
    last_scan TEXT NOT NULL,
    notes TEXT NOT NULL
);

WITH RECURSIVE sequence(n) AS (
    VALUES (1)
    UNION ALL
    SELECT n + 1 FROM sequence WHERE n < 900
)
INSERT INTO shipments
    (id, name, location, ship_date, status, carrier, service_level, last_scan, notes)
SELECT
    printf('shp_%08x', 1100000 + n),
    printf(
        '%s %04d',
        CASE n % 12
            WHEN 0 THEN 'Archive Carton'
            WHEN 1 THEN 'Beacon Sample Case'
            WHEN 2 THEN 'Cedar Display Panel'
            WHEN 3 THEN 'Field Kit'
            WHEN 4 THEN 'Gallery Lighting Pack'
            WHEN 5 THEN 'Harbor Supply Tote'
            WHEN 6 THEN 'Juniper Document Tube'
            WHEN 7 THEN 'Kestrel Equipment Box'
            WHEN 8 THEN 'Museum Textile Roll'
            WHEN 9 THEN 'Orchard Exhibit Crate'
            WHEN 10 THEN 'Research Parcel'
            ELSE 'Volunteer Material Bin'
        END,
        n
    ),
    CASE n % 10
        WHEN 0 THEN 'Portland'
        WHEN 1 THEN 'Seattle'
        WHEN 2 THEN 'Denver'
        WHEN 3 THEN 'Austin'
        WHEN 4 THEN 'Raleigh'
        WHEN 5 THEN 'Chicago'
        WHEN 6 THEN 'Boston'
        WHEN 7 THEN 'Phoenix'
        WHEN 8 THEN 'Boise'
        ELSE 'Sacramento'
    END,
    printf('2026-%02d-%02d', 1 + (n % 12), 1 + (n % 27)),
    CASE n % 6
        WHEN 0 THEN 'active'
        WHEN 1 THEN 'delivered'
        WHEN 2 THEN 'archived'
        WHEN 3 THEN 'paused'
        WHEN 4 THEN 'returned'
        ELSE 'in-transit'
    END,
    CASE n % 4
        WHEN 0 THEN 'Northline Freight'
        WHEN 1 THEN 'Pioneer Parcel'
        WHEN 2 THEN 'Cascade Logistics'
        ELSE 'Continental Dispatch'
    END,
    CASE n % 3
        WHEN 0 THEN 'standard'
        WHEN 1 THEN 'priority'
        ELSE 'controlled'
    END,
    printf('Sorting checkpoint %03d', n % 137),
    printf('Routine generated shipment record %04d.', n)
FROM sequence;

INSERT INTO shipments
    (id, name, location, ship_date, status, carrier, service_level, last_scan, notes)
VALUES
    ('shp_9d2f7a61', 'Parcel Driftwood', 'Portland', '2026-07-18', 'active',
     'Cascade Logistics', 'priority', 'Portland exception desk',
     'Manual review cleared; retain original routing label.'),
    ('shp_15c0a4e2', 'Parcel Driftwood', 'Seattle', '2026-07-17', 'active',
     'Pioneer Parcel', 'standard', 'Seattle north dock',
     'Same-name shipment at another location.'),
    ('shp_36a18bd4', 'Parcel Driftwood', 'Denver', '2026-07-16', 'active',
     'Continental Dispatch', 'standard', 'Denver transfer floor',
     'Same-name shipment at another location.'),
    ('shp_a01ee493', 'Parcel Driftwood', 'Portland', '2026-06-28', 'archived',
     'Cascade Logistics', 'standard', 'Portland records cage',
     'Prior shipment with the exact name and a different status.'),
    ('shp_62f01b8c', 'Parcel Driftwood', 'Portland', '2026-06-30', 'paused',
     'Northline Freight', 'controlled', 'Portland review lane',
     'Exact name and location with a different status.'),
    ('shp_83b19d70', 'Parcel Drift Wood', 'Portland', '2026-07-11', 'active',
     'Cascade Logistics', 'priority', 'Portland outbound dock',
     'Spacing variant retained as a distractor.'),
    ('shp_7e3a014f', 'Parcel Driftwoods', 'Portland', '2026-07-12', 'active',
     'Pioneer Parcel', 'standard', 'Portland outbound dock',
     'Pluralized name retained as a distractor.'),
    ('shp_2b74c90a', 'Parcel Driftwood Return', 'Portland', '2026-07-13', 'active',
     'Northline Freight', 'controlled', 'Portland returns desk',
     'Longer similar name retained as a distractor.'),
    ('shp_f5c8821d', 'Parcel-Driftwood', 'Portland', '2026-07-14', 'active',
     'Continental Dispatch', 'priority', 'Portland outbound dock',
     'Punctuation variant retained as a distractor.'),
    ('shp_0da77c51', 'Parcel Driftwood', 'Portland, OR', '2026-07-15', 'active',
     'Cascade Logistics', 'standard', 'Metro regional dock',
     'Similar location label retained as a distractor.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('exception-desk', 'active-portland-first');

CREATE TABLE availability (
    location TEXT NOT NULL,
    ship_date TEXT NOT NULL,
    dock_capacity INTEGER NOT NULL,
    PRIMARY KEY (location, ship_date)
);

INSERT INTO availability (location, ship_date, dock_capacity)
VALUES
    ('Portland', '2026-07-18', 3),
    ('Seattle', '2026-07-18', 5);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    note TEXT NOT NULL
);
