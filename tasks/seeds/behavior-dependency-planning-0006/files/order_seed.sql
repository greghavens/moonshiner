PRAGMA foreign_keys = ON;

CREATE TABLE purchase_orders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_for TEXT NOT NULL,
    vendor TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    total_cents INTEGER NOT NULL
);

CREATE TABLE preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE availability (
    sku TEXT NOT NULL,
    location TEXT NOT NULL,
    units INTEGER NOT NULL,
    PRIMARY KEY (sku, location)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    order_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO purchase_orders
    (id, name, location, status, requested_for, vendor, item_count, total_cents)
VALUES
    ('ord-216', 'Ergonomic Chair Order', 'Boise Office', 'approved',
     '2026-07-22', 'Northwest Workplace Supply', 24, 1031760),
    ('ord-684', 'New-Hire Welcome Kit Order', 'Phoenix Branch', 'backordered',
     '2026-07-23', 'Sonoran Onboarding Goods', 40, 348000),
    ('ord-906', 'Ergonomic Chair Order', 'Boise Annex', 'cancelled',
     '2025-11-04', 'Northwest Workplace Supply', 8, 343920),
    ('ord-848', 'New-Hire Welcome Kit Order', 'Phoenix Distribution Center', 'fulfilled',
     '2025-12-12', 'Sonoran Onboarding Goods', 30, 261000);

INSERT INTO preferences (preference_key, preference_value) VALUES
    ('default_cost_center', 'Facilities'),
    ('review_timezone', 'America/Denver');

INSERT INTO availability (sku, location, units) VALUES
    ('CHAIR-ERG-4', 'Boise Office', 31),
    ('WELCOME-KIT-2', 'Phoenix Branch', 0);
