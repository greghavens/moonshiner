PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    asset_tag TEXT NOT NULL UNIQUE,
    vehicle_class TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    open_vehicles INTEGER NOT NULL,
    PRIMARY KEY (location, service_date)
);

CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    preference TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation TEXT NOT NULL UNIQUE,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    parent_start_ticks INTEGER NOT NULL,
    result_count INTEGER,
    exact_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT,
    violation INTEGER NOT NULL DEFAULT 0
);

INSERT INTO vehicles
    (stable_id, name, location, status, record_date, asset_tag,
     vehicle_class, capacity, notes)
VALUES
    (
        'fle-120',
        'Shuttle 30',
        'Depot D',
        'active',
        '2026-05-17',
        'DD-SH-0030',
        'passenger shuttle',
        30,
        'Primary Depot D shuttle; inspection current through 2026-11-30.'
    ),
    (
        'fle-1700',
        'Shuttle 30',
        'Depot A',
        'pending',
        '2026-01-22',
        'DA-SH-0030',
        'passenger shuttle',
        30,
        'Awaiting commissioning at Depot A.'
    ),
    (
        'fle-1701',
        'Shuttle 30',
        'Depot B',
        'closed',
        '2026-02-03',
        'DB-SH-0030',
        'passenger shuttle',
        30,
        'Retired Depot B shuttle record.'
    ),
    (
        'fle-1702',
        'Shuttle 30',
        'Depot C',
        'active',
        '2026-03-08',
        'DC-SH-0030',
        'passenger shuttle',
        30,
        'Separate active shuttle assigned to Depot C.'
    ),
    (
        'fle-1703',
        'Shuttle 30 — draft',
        'Depot D',
        'pending',
        '2026-10-22',
        'DD-DRAFT-0030',
        'planning record',
        0,
        'Draft replacement proposal, not the requested vehicle.'
    ),
    (
        'fle-1704',
        'Shuttle 30 notes',
        'Depot D',
        'closed',
        '2026-11-26',
        'DD-NOTES-0030',
        'administrative record',
        0,
        'Imported notes record with a similar label.'
    ),
    (
        'fle-1705',
        'Archive: Shuttle 30',
        'Depot D',
        'active',
        '2025-12-05',
        'DD-ARCH-0030',
        'archive record',
        0,
        'Historical snapshot whose archived status value is not live state.'
    ),
    (
        'fle-1706',
        'Shuttle 30 follow-up',
        'Depot D',
        'pending',
        '2026-01-09',
        'DD-FOLLOW-0030',
        'work item',
        0,
        'Maintenance follow-up record.'
    ),
    (
        'fle-1707',
        'Shuttle 30 renewal',
        'Depot D',
        'closed',
        '2026-02-13',
        'DD-RENEW-0030',
        'work item',
        0,
        'Completed permit-renewal record.'
    ),
    (
        'fle-1708',
        'Shuttle 30 briefing',
        'Depot D',
        'active',
        '2026-03-17',
        'DD-BRIEF-0030',
        'administrative record',
        0,
        'Briefing registration entry, not a vehicle.'
    );

WITH RECURSIVE sequence(value) AS (
    SELECT 1
    UNION ALL
    SELECT value + 1 FROM sequence WHERE value < 72
)
INSERT INTO vehicles
    (stable_id, name, location, status, record_date, asset_tag,
     vehicle_class, capacity, notes)
SELECT
    printf('fle-%04d', 3000 + value),
    printf('Shuttle 30 review %03d', value),
    'Depot D',
    CASE value % 3
        WHEN 0 THEN 'active'
        WHEN 1 THEN 'pending'
        ELSE 'closed'
    END,
    printf(
        '2026-%02d-%02d',
        ((value - 1) % 12) + 1,
        ((value * 7) % 28) + 1
    ),
    printf('DD-REVIEW-%04d', value),
    'review record',
    0,
    printf('Generated review distractor %03d; not a vehicle asset.', value)
FROM sequence;

WITH RECURSIVE sequence(value) AS (
    SELECT 1
    UNION ALL
    SELECT value + 1 FROM sequence WHERE value < 180
)
INSERT INTO vehicles
    (stable_id, name, location, status, record_date, asset_tag,
     vehicle_class, capacity, notes)
SELECT
    printf('fle-%04d', 4000 + value),
    CASE value % 4
        WHEN 0 THEN printf('Service Van %03d', value)
        WHEN 1 THEN printf('Cargo Truck %03d', value)
        WHEN 2 THEN printf('Pool Sedan %03d', value)
        ELSE printf('Yard Tractor %03d', value)
    END,
    CASE value % 4
        WHEN 0 THEN 'Depot A'
        WHEN 1 THEN 'Depot B'
        WHEN 2 THEN 'Depot C'
        ELSE 'Depot D'
    END,
    CASE value % 3
        WHEN 0 THEN 'active'
        WHEN 1 THEN 'pending'
        ELSE 'closed'
    END,
    printf(
        '2025-%02d-%02d',
        ((value + 2) % 12) + 1,
        ((value * 11) % 28) + 1
    ),
    printf('NOISE-%04d', value),
    CASE value % 4
        WHEN 0 THEN 'service van'
        WHEN 1 THEN 'cargo truck'
        WHEN 2 THEN 'pool sedan'
        ELSE 'yard tractor'
    END,
    2 + (value % 12),
    printf('Unrelated fleet fixture record %03d.', value)
FROM sequence;

INSERT INTO availability (location, service_date, open_vehicles)
VALUES
    ('Depot A', '2026-07-25', 4),
    ('Depot B', '2026-07-25', 2),
    ('Depot C', '2026-07-25', 5),
    ('Depot D', '2026-07-25', 3);

INSERT INTO profiles (profile_id, display_name, preference)
VALUES
    ('driver-104', 'Alex Morgan', 'morning'),
    ('dispatcher-208', 'Riley Chen', 'radio');
