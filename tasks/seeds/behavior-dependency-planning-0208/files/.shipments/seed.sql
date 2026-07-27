PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    stable_id TEXT PRIMARY KEY,
    shipment TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL,
    shipment_date TEXT NOT NULL,
    carrier TEXT NOT NULL,
    service TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (
        lifecycle IN ('current', 'draft', 'archived', 'cancelled')
    )
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES shipments(stable_id),
    message TEXT NOT NULL
);

INSERT INTO shipments
    (stable_id, shipment, city, status, shipment_date,
     carrier, service, lifecycle)
VALUES
    ('shp-208', 'Library transfer cartons', 'Portland', 'in-transit',
     '2026-07-24', 'Northstar Freight', 'regional ground', 'current'),
    ('shp-608', 'Vaccine cooler shipment', 'Denver', 'delayed',
     '2026-07-23', 'Alpine Medical Logistics', 'cold-chain priority', 'current'),
    ('shp-208-draft', 'Library transfer cartons', 'Portland', 'draft',
     '2026-07-29', 'Unassigned', 'regional ground', 'draft'),
    ('shp-318', 'Library transfer cartons', 'Salem', 'scheduled',
     '2026-07-25', 'Northstar Freight', 'regional ground', 'current'),
    ('shp-427', 'Library transfer carton', 'Portland', 'delivered',
     '2026-07-22', 'CityLine Courier', 'local', 'current'),
    ('shp-518', 'Library transfer cartons', 'Portland', 'delivered',
     '2025-07-24', 'Northstar Freight', 'regional ground', 'archived'),
    ('shp-608-draft', 'Vaccine cooler shipment', 'Denver', 'draft',
     '2026-07-30', 'Unassigned', 'cold-chain priority', 'draft'),
    ('shp-746', 'Vaccine cooler shipment', 'Aurora', 'scheduled',
     '2026-07-23', 'Alpine Medical Logistics', 'cold-chain priority', 'current'),
    ('shp-804', 'Vaccine cooler shipment', 'Denver', 'delivered',
     '2025-07-23', 'Alpine Medical Logistics', 'cold-chain priority', 'archived'),
    ('shp-917', 'Vaccine coolers shipment', 'Denver', 'in-transit',
     '2026-07-26', 'Mile High Express', 'refrigerated', 'current');
