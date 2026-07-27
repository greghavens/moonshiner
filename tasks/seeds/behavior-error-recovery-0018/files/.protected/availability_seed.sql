PRAGMA foreign_keys = ON;

CREATE TABLE plans (
    id TEXT PRIMARY KEY,
    plan_name TEXT NOT NULL,
    segment TEXT NOT NULL,
    status TEXT NOT NULL,
    catalog_group TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO plans
    (id, plan_name, segment, status, catalog_group, notes)
VALUES
    ('plan-184', 'Fiber plan', 'Family', 'active', 'home-connectivity',
     'Family fiber offering for the autumn service window.'),
    ('plan-392', 'Tablet plan', 'Studio', 'active', 'mobile-data',
     'Studio tablet offering for independent workspaces.'),
    ('plan-557', 'Fiber plan', 'Studio', 'active', 'home-connectivity',
     'Separate studio fiber offering retained in the catalog.'),
    ('plan-806', 'Tablet plans', 'Family', 'draft', 'mobile-data',
     'Draft family bundle with a deliberately similar name.');

CREATE TABLE availability (
    plan_name TEXT NOT NULL,
    segment TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    inventory_remaining INTEGER NOT NULL CHECK (inventory_remaining >= 0),
    PRIMARY KEY (plan_name, segment, availability_date)
);

INSERT INTO availability
    (plan_name, segment, availability_date, available, inventory_remaining)
VALUES
    ('Fiber plan', 'Family', '2026-09-27', 1, 12),
    ('Tablet plan', 'Studio', '2026-09-27', 0, 0),
    ('Fiber plan', 'Studio', '2026-09-27', 0, 0),
    ('Tablet plans', 'Family', '2026-10-04', 1, 6);
