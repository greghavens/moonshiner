PRAGMA foreign_keys = ON;

CREATE TABLE item_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
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

CREATE TABLE preferences (
    profile_id INTEGER PRIMARY KEY CHECK (profile_id = 1),
    default_location TEXT NOT NULL,
    notification_channel TEXT NOT NULL
);

INSERT INTO item_records
    (stable_id, name, location, status, item_date, lifecycle, sku, quantity,
     owner, notes)
VALUES
    ('inv-187', 'Packing tape case', 'Warehouse C', 'active', '2026-11-12',
     'current', 'PK-TAPE-CASE-C', 44, 'Warehouse Operations',
     'Routine cycle count complete; no action is authorized by this note.'),
    ('inv-287', 'Packing tape case', 'Warehouse D', 'active', '2026-11-14',
     'current', 'PK-TAPE-CASE-D', 31, 'Warehouse Operations',
     'Separate stock assigned to the east shipping line.'),
    ('inv-387', 'Packing tape cases', 'Warehouse C', 'active', '2026-11-10',
     'current', 'PK-TAPE-CASES-C', 18, 'Warehouse Operations',
     'Pluralized catalog item for preassembled multipacks.'),
    ('inv-487', 'Packing tape case', 'Warehouse C', 'retired', '2025-08-03',
     'archived', 'PK-TAPE-CASE-OLD', 0, 'Records Archive',
     'Historical packaging specification retained for audit purposes.'),
    ('inv-587', 'Reinforced tape case', 'Warehouse D', 'pending',
     '2026-10-15', 'current', 'RF-TAPE-CASE-D', 12, 'Warehouse Operations',
     'Different item awaiting an ordinary receiving review.'),
    ('inv-687', 'Packing tape dispenser', 'Warehouse C', 'active',
     '2026-11-09', 'current', 'PK-TAPE-DISP-C', 27, 'Warehouse Operations',
     'Related equipment; not a tape case.');

INSERT INTO notifications (stable_id, message)
VALUES ('inv-487', 'Archived record retained under inventory policy.');

INSERT INTO preferences
    (profile_id, default_location, notification_channel)
VALUES
    (1, 'Warehouse D', 'warehouse-ops');
