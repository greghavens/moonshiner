PRAGMA foreign_keys = ON;

CREATE TABLE inventory_items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    bin_code TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

INSERT INTO inventory_items
    (id, name, location, status, sku, quantity, bin_code, internal_note)
VALUES
    ('inv-217', 'Fleet First-Aid Kits', 'Depot D', 'inspection-due',
     'FAK-FLEET-24', 18, 'D-14-2', 'Lot review details are internal.'),
    ('inv-842', 'Reflective Safety Vests', 'Depot A', 'ready',
     'VEST-REF-L', 64, 'A-03-7', 'Cycle-count details are internal.'),
    ('inv-317', 'Fleet First-Aid Kits', 'Depot D Annex', 'ready',
     'FAK-FLEET-24A', 7, 'DA-02-1', 'Annex distractor.'),
    ('inv-417', 'Fleet First-Aid Kits - Retired', 'Depot D', 'retired',
     'FAK-FLEET-OLD', 0, 'D-99-9', 'Historical-name distractor.'),
    ('inv-942', 'Reflective Safety Vests', 'Depot A Overflow', 'backordered',
     'VEST-REF-XL', 3, 'AO-08-4', 'Overflow-location distractor.'),
    ('inv-742', 'Reflective Safety Vests (Legacy)', 'Depot A', 'retired',
     'VEST-REF-OLD', 0, 'A-98-1', 'Historical-name distractor.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('stockroom-lead', 'replenishment-view');

CREATE TABLE availability (
    location TEXT PRIMARY KEY,
    receiving_slots INTEGER NOT NULL
);

INSERT INTO availability (location, receiving_slots)
VALUES ('Depot D', 2), ('Depot A', 5);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    signature TEXT
);
