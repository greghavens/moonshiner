PRAGMA foreign_keys = ON;

CREATE TABLE inventory_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'archived')),
    storage_bin TEXT NOT NULL,
    steward TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES inventory_records(stable_id)
);

INSERT INTO inventory_records
    (stable_id, name, location, status, lifecycle, storage_bin, steward)
VALUES
    ('inv-227', 'Nitrile work gloves', 'Warehouse D', 'in-stock',
     'active', 'D-14', 'Avery Morgan'),
    ('inv-627', 'Shipping labels', 'Supply Room 2', 'reorder-needed',
     'active', 'SR2-08', 'Jordan Kim'),
    ('inv-1027', 'Nitrile work gloves archive', 'North Depot', 'closed',
     'archived', 'ND-A3', 'Records Team'),
    ('inv-1227', 'Nitrile work glove liners', 'Warehouse D', 'in-stock',
     'active', 'D-15', 'Avery Morgan'),
    ('inv-1627', 'Nitrile work gloves', 'Warehouse C', 'low-stock',
     'active', 'C-09', 'Taylor Reed'),
    ('inv-2027', 'Shipping labels', 'Supply Room 5', 'in-stock',
     'active', 'SR5-11', 'Jordan Kim'),
    ('inv-2427', 'Shipping labels - legacy rolls', 'Supply Room 2', 'retired',
     'archived', 'SR2-A1', 'Records Team');
