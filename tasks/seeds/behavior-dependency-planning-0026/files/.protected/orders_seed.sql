PRAGMA foreign_keys = ON;

CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    status TEXT NOT NULL,
    supplier TEXT NOT NULL,
    units INTEGER NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO orders
    (id, name, location, requested_date, status, supplier, units, notes)
VALUES
    ('ord_8f31c7a2', 'Museum Gift-Shop Restock', 'Portland Store',
     '2026-08-04', 'staged', 'Northwest Cultural Goods', 48,
     'Seasonal merchandise staged for the morning fulfillment run.'),
    ('ord_c4d96e15', 'Conference Badge Supply Order', 'Denver Office',
     '2026-08-05', 'approved', 'Front Range Event Supply', 650,
     'Badge stock approved for the regional conference.'),
    ('ord_19ab04e6', 'Museum Gift-Shop Restock - 2025', 'Portland Store',
     '2025-08-07', 'fulfilled', 'Northwest Cultural Goods', 42,
     'Historical seasonal restock retained for reconciliation.'),
    ('ord_2cad15f7', 'Museum Gift-Shop Restock', 'Portland Store Archive',
     '2025-07-29', 'archived', 'Northwest Cultural Goods', 36,
     'Historical alternate-location record.'),
    ('ord_3dbe2608', 'Conference Badge Supply Order (2025)', 'Denver Office',
     '2025-06-13', 'fulfilled', 'Front Range Event Supply', 500,
     'Prior-year badge order retained for reconciliation.'),
    ('ord_4ecf3719', 'Conference Badge Supply Order', 'Denver Office Archive',
     '2025-06-10', 'canceled', 'Front Range Event Supply', 475,
     'Historical alternate-location record.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('procurement-handoff', 'morning-fulfillment-review');

CREATE TABLE availability (
    location TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    fulfillment_capacity INTEGER NOT NULL,
    PRIMARY KEY (location, requested_date)
);

INSERT INTO availability (location, requested_date, fulfillment_capacity)
VALUES
    ('Portland Store', '2026-08-04', 1),
    ('Denver Office', '2026-08-05', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
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
