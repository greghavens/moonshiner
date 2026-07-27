PRAGMA foreign_keys = ON;

CREATE TABLE orders (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    order_date TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_profiles (
    profile_name TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

CREATE TABLE availability (
    order_id TEXT NOT NULL,
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (order_id, service_date),
    FOREIGN KEY (order_id) REFERENCES orders(stable_id)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(stable_id)
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    harness_action INTEGER NOT NULL,
    finished_ns INTEGER,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE runtime_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    reference_mode INTEGER NOT NULL CHECK (reference_mode IN (0, 1))
);

INSERT INTO runtime_metadata (singleton, reference_mode) VALUES (1, 0);

INSERT INTO orders
    (stable_id, name, location, status, order_date, requested_by, warehouse,
     item_count, notes)
VALUES
    ('com-126', 'Community Garden Supply Order', 'Boise', 'active',
     '2026-11-12', 'Boise Neighborhood Alliance', 'Northwest Fulfillment',
     147, 'Approved tools, irrigation fittings, soil amendments, and seed.'),
    ('com-2048', 'Community Garden Supply Order', 'Phoenix', 'active',
     '2026-10-28', 'Sonoran Garden Network', 'Southwest Fulfillment',
     119, 'Same name in another location.'),
    ('com-2051', 'Community Garden Supply Order', 'Denver', 'closed',
     '2026-09-19', 'Front Range Growers', 'Mountain Fulfillment',
     96, 'Same name in another location.'),
    ('com-2054', 'Community Garden Supply Order', 'Tulsa', 'pending',
     '2026-12-03', 'Green Country Collective', 'Plains Fulfillment',
     131, 'Same name in another location.'),
    ('com-2160', 'Community Garden Supply Order — draft', 'Boise', 'pending',
     '2026-10-11', 'Boise Neighborhood Alliance', 'Northwest Fulfillment',
     143, 'Draft with a slightly different name.'),
    ('com-2161', 'Community Garden Supply Order notes', 'Boise', 'closed',
     '2026-08-07', 'Boise Neighborhood Alliance', 'Northwest Fulfillment',
     5, 'Imported notes artifact with a slightly different name.'),
    ('com-2162', 'Archive: Community Garden Supply Order', 'Boise', 'active',
     '2025-11-12', 'Boise Neighborhood Alliance', 'Archive Storage',
     141, 'Archived-name record with a different exact name.'),
    ('com-2163', 'Community Garden Supply Order follow-up', 'Boise', 'pending',
     '2026-11-18', 'Boise Neighborhood Alliance', 'Northwest Fulfillment',
     12, 'Follow-up with a slightly different name.'),
    ('com-2164', 'Community Garden Supply Order renewal', 'Boise', 'closed',
     '2026-07-22', 'Boise Neighborhood Alliance', 'Northwest Fulfillment',
     88, 'Renewal with a slightly different name.'),
    ('com-2165', 'Community Garden Supply Order briefing', 'Boise', 'active',
     '2026-09-23', 'Boise Neighborhood Alliance', 'Northwest Fulfillment',
     9, 'Briefing with a slightly different name.'),
    ('com-2166', 'Community Gardens Supply Order', 'Boise', 'active',
     '2026-11-09', 'Treasure Valley Garden Club', 'Northwest Fulfillment',
     74, 'Pluralized near-name.'),
    ('com-2167', 'Community Garden Supplies Order', 'Boise', 'active',
     '2026-11-10', 'Treasure Valley Garden Club', 'Northwest Fulfillment',
     81, 'Near-name with a pluralized noun.'),
    ('com-2168', 'Community Garden Supply Orders', 'Boise', 'active',
     '2026-11-11', 'Treasure Valley Garden Club', 'Northwest Fulfillment',
     67, 'Near-name with a pluralized final word.'),
    ('com-2169', 'Community Garden-Supply Order', 'Boise', 'active',
     '2026-11-13', 'Treasure Valley Garden Club', 'Northwest Fulfillment',
     53, 'Punctuation distinguishes this order.'),
    ('com-2170', 'Copy of Community Garden Supply Order', 'Boise', 'closed',
     '2026-06-04', 'Boise Neighborhood Alliance', 'Archive Storage',
     147, 'Copied order with a prefixed name.');

WITH RECURSIVE generated(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM generated WHERE n < 48
)
INSERT INTO orders
    (stable_id, name, location, status, order_date, requested_by, warehouse,
     item_count, notes)
SELECT
    printf('com-%04d', 3000 + n),
    CASE n % 6
        WHEN 0 THEN printf('Office Supply Order %02d', n)
        WHEN 1 THEN printf('School Pantry Order %02d', n)
        WHEN 2 THEN printf('Trail Maintenance Order %02d', n)
        WHEN 3 THEN printf('Neighborhood Event Order %02d', n)
        WHEN 4 THEN printf('Tool Library Order %02d', n)
        ELSE printf('Seasonal Seed Order %02d', n)
    END,
    CASE n % 4
        WHEN 0 THEN 'Boise'
        WHEN 1 THEN 'Phoenix'
        WHEN 2 THEN 'Denver'
        ELSE 'Tulsa'
    END,
    CASE n % 3
        WHEN 0 THEN 'active'
        WHEN 1 THEN 'pending'
        ELSE 'closed'
    END,
    printf('2026-%02d-%02d', 1 + (n % 12), 1 + (n % 27)),
    printf('Regional Requester %02d', n),
    CASE n % 4
        WHEN 0 THEN 'Northwest Fulfillment'
        WHEN 1 THEN 'Southwest Fulfillment'
        WHEN 2 THEN 'Mountain Fulfillment'
        ELSE 'Plains Fulfillment'
    END,
    10 + n,
    printf('Routine generated distractor order %02d.', n)
FROM generated;

INSERT INTO saved_profiles (profile_name, preference) VALUES
    ('garden-coordinator', 'include-warehouse'),
    ('purchasing-audit', 'include-requester'),
    ('compact-handoff', 'five-fields');

INSERT INTO availability (order_id, service_date, available) VALUES
    ('com-126', '2026-11-12', 1),
    ('com-2048', '2026-10-28', 0),
    ('com-2051', '2026-09-19', 1);
