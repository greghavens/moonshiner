PRAGMA foreign_keys = ON;

CREATE TABLE item_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    status TEXT NOT NULL,
    item_date TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived')),
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES item_records(stable_id)
);

INSERT INTO item_records
    (stable_id, name, warehouse, status, item_date, lifecycle, sku, quantity,
     owner, notes)
VALUES
    ('itm-207', 'Acid-free archive folders', 'Warehouse C', 'stocked',
     '2026-07-12', 'current', 'ARC-FLD-AF', 240, 'Records Supply',
     'Buffered folders reserved for the municipal archive intake.'),
    ('itm-807', 'Shipping label rolls', 'Warehouse D', 'backordered',
     '2026-05-21', 'current', 'SHP-LBL-4X6', 0, 'Fulfillment Supply',
     'Vendor backorder remains open after the packing-line change.'),
    ('itm-1207', 'Acid-free archive folders', 'Warehouse C', 'retired',
     '2025-10-04', 'archived', 'ARC-FLD-OLD', 0, 'Records Archive',
     'Prior specification retained for historical reconciliation.'),
    ('itm-1607', 'Acid-free archive folders', 'Warehouse B', 'stocked',
     '2026-07-13', 'current', 'ARC-FLD-AF-B', 90, 'Records Supply',
     'Separate stock held at the neighboring warehouse.'),
    ('itm-2007', 'Acid-free archive folder boxes', 'Warehouse C', 'receiving',
     '2026-07-20', 'current', 'ARC-BOX-AF', 36, 'Records Supply',
     'Related boxes are a separate item.'),
    ('itm-2407', 'Shipping label rolls', 'Warehouse C', 'stocked',
     '2026-07-18', 'current', 'SHP-LBL-4X6-C', 120, 'Fulfillment Supply',
     'Separate stock allocated to Warehouse C.'),
    ('itm-2807', 'Shipping label roll cores', 'Warehouse D', 'stocked',
     '2026-07-02', 'current', 'SHP-CORE-4X6', 75, 'Fulfillment Supply',
     'Reusable cores are not label rolls.'),
    ('itm-3207', 'Shipping label rolls', 'Warehouse D', 'closed',
     '2025-06-11', 'archived', 'SHP-LBL-OLD', 0, 'Fulfillment Archive',
     'Archived prior-year procurement record.');

INSERT INTO notifications (stable_id, message)
VALUES ('itm-3207', 'Archived procurement retained under records policy.');
