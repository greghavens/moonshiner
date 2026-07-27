PRAGMA foreign_keys = ON;

CREATE TABLE item_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    store TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    supplier TEXT NOT NULL,
    description TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES item_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO item_records
    (stable_id, name, store, status, record_date, sku, quantity, supplier,
     description, lifecycle)
VALUES
    ('itm-227-a4', 'Nitrile examination gloves', 'Clinic Store', 'available', '2026-07-19', 'CLN-NIT-100', 480, 'Medline Basin Supply', 'Powder-free examination gloves in mixed clinic sizes.', 'current'),
    ('itm-227-b8', 'Portable radio batteries', 'Depot Store', 'backordered', '2026-07-19', 'DEP-RAD-240', 36, 'Front Range Power', 'Rechargeable battery packs for portable operations radios.', 'current'),
    ('itm-227-c2', 'Nitrile examination glove', 'Clinic Store', 'low-stock', '2026-07-18', 'CLN-NIT-010', 12, 'Medline Basin Supply', 'Singular-name glove inventory record.', 'current'),
    ('itm-227-d6', 'Nitrile examination gloves', 'Surgery Store', 'available', '2026-07-20', 'SUR-NIT-100', 920, 'Surgical Mesa Supply', 'Same item name assigned to a different store.', 'current'),
    ('itm-227-e1', 'Nitrile examination gloves', 'Clinic Store', 'retired', '2025-07-19', 'CLN-NIT-OLD', 0, 'Legacy Clinical Supply', 'Archived record for the exact name and store.', 'archived'),
    ('itm-227-f5', 'Portable radios batteries', 'Depot Store', 'available', '2026-07-21', 'DEP-RADS-240', 70, 'Front Range Power', 'Pluralized radio-name inventory record.', 'current'),
    ('itm-227-g9', 'Portable radio batteries', 'Field Store', 'available', '2026-07-21', 'FLD-RAD-240', 61, 'Front Range Power', 'Same item name assigned to a different store.', 'current'),
    ('itm-227-h3', 'Portable radio battery chargers', 'Depot Store', 'available', '2026-07-20', 'DEP-RAD-CHG', 18, 'Front Range Power', 'Related charging equipment.', 'current'),
    ('itm-227-j7', 'Portable radio batteries', 'Depot Store', 'retired', '2025-07-19', 'DEP-RAD-OLD', 0, 'Legacy Depot Supply', 'Archived record for the exact name and store.', 'archived'),
    ('itm-227-k4', 'Sterile gauze pads', 'Clinic Store', 'available', '2026-07-17', 'CLN-GAU-050', 650, 'Medline Basin Supply', 'Separate clinic consumable.', 'current');
