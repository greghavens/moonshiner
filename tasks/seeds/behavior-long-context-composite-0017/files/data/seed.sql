PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE facilities_requests (
    request_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    request_date TEXT NOT NULL
);

CREATE TABLE mutation_log (
    entry_id INTEGER PRIMARY KEY,
    operation TEXT NOT NULL,
    request_id TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE notification_log (
    entry_id INTEGER PRIMARY KEY,
    request_id TEXT,
    recipient TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

INSERT INTO metadata (key, value) VALUES
    ('receipt_key', 'facilitydesk-lcc-0017-6a402f8c3d15');

INSERT INTO facilities_requests (
    request_id, name, location, status, request_date
) VALUES
    (
        'fr-9c2a71',
        'East Wing Lighting Repair',
        'Building A',
        'active',
        '2026-07-21'
    ),
    (
        'fr-x1001',
        'East Wing Lighting Repair',
        'Building B',
        'pending',
        '2026-07-06'
    ),
    (
        'fr-x1002',
        'East Wing Lighting Repair',
        'Building C',
        'closed',
        '2026-06-18'
    ),
    (
        'fr-x1003',
        'East Wing Lighting Repair',
        'Building D',
        'active',
        '2026-07-19'
    ),
    (
        'fr-x1004',
        'East Wing Lighting Repair — draft',
        'Building A',
        'pending',
        '2026-07-20'
    ),
    (
        'fr-x1005',
        'East Wing Lighting Repair notes',
        'Building A',
        'closed',
        '2026-06-30'
    ),
    (
        'fr-x1006',
        'Archive: East Wing Lighting Repair',
        'Building A',
        'active',
        '2026-05-11'
    ),
    (
        'fr-x1007',
        'East Wing Lighting Repair follow-up',
        'Building A',
        'pending',
        '2026-07-22'
    ),
    (
        'fr-x1008',
        'East Wing Lighting Repair renewal',
        'Building A',
        'closed',
        '2026-04-03'
    ),
    (
        'fr-x1009',
        'East Wing Lighting Repair briefing',
        'Building A',
        'active',
        '2026-07-23'
    ),
    (
        'fr-x1010',
        'West Wing Lighting Repair',
        'Building A',
        'active',
        '2026-07-12'
    ),
    (
        'fr-x1011',
        'East Wing Emergency Lighting Repair',
        'Building A',
        'active',
        '2026-07-18'
    ),
    (
        'fr-x1012',
        'East Wing Lighting Inspection',
        'Building A',
        'pending',
        '2026-07-24'
    );

WITH RECURSIVE sequence(number) AS (
    SELECT 1
    UNION ALL
    SELECT number + 1 FROM sequence WHERE number < 72
)
INSERT INTO facilities_requests (
    request_id, name, location, status, request_date
)
SELECT
    printf('fr-r%04d', number),
    CASE number % 6
        WHEN 0 THEN 'Badge reader check'
        WHEN 1 THEN 'Temperature sensor review'
        WHEN 2 THEN 'Conference room setup'
        WHEN 3 THEN 'Loading dock inspection'
        WHEN 4 THEN 'Elevator service follow-up'
        ELSE 'Exterior lighting review'
    END || printf(' %02d', number),
    CASE number % 4
        WHEN 0 THEN 'Building A'
        WHEN 1 THEN 'Building B'
        WHEN 2 THEN 'Building C'
        ELSE 'Building D'
    END,
    CASE number % 3
        WHEN 0 THEN 'active'
        WHEN 1 THEN 'pending'
        ELSE 'closed'
    END,
    printf('2026-%02d-%02d', ((number - 1) % 7) + 1, ((number * 5) % 27) + 1)
FROM sequence;

PRAGMA user_version = 1;
