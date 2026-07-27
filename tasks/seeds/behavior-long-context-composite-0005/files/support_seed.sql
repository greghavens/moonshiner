PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    case_date TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner TEXT NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE saved_profiles (
    location TEXT PRIMARY KEY,
    escalation_channel TEXT NOT NULL,
    service_tier TEXT NOT NULL
);

CREATE TABLE availability (
    team TEXT NOT NULL,
    shift_date TEXT NOT NULL,
    agents_available INTEGER NOT NULL,
    PRIMARY KEY (team, shift_date)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    case_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO cases
    (id, name, location, case_date, status, priority, owner, summary)
VALUES
    ('sup-105', 'Crushed Carton Follow-up', 'Acme', '2026-08-18',
     'active', 'high', 'Mira Chen',
     'Replacement carton approved; carrier damage photos are due Friday.'),
    ('sup-950', 'Crushed Carton Follow-up', 'Beacon', '2026-10-07',
     'pending', 'normal', 'Jon Bell', 'Awaiting warehouse count.'),
    ('sup-951', 'Crushed Carton Follow-up', 'Cedar', '2026-11-12',
     'closed', 'low', 'Inez Park', 'Credit completed.'),
    ('sup-952', 'Crushed Carton Follow-up', 'Delta', '2026-12-17',
     'pending', 'normal', 'Omar West', 'Return label requested.'),
    ('sup-953', 'Crushed Carton Follow-up — draft', 'Acme', '2026-07-07',
     'pending', 'low', 'Nia Cole', 'Draft intake awaiting review.'),
    ('sup-954', 'Crushed Carton Follow-up notes', 'Acme', '2026-08-11',
     'closed', 'low', 'Nia Cole', 'Internal note conversion complete.'),
    ('sup-955', 'Archive: Crushed Carton Follow-up', 'Acme', '2026-09-15',
     'active', 'normal', 'Luis Gray', 'Archive reconciliation in progress.'),
    ('sup-956', 'Crushed Carton Follow-up follow-up', 'Acme', '2026-10-19',
     'pending', 'normal', 'Tara Fox', 'Secondary follow-up queued.'),
    ('sup-957', 'Crushed Carton Follow-up renewal', 'Acme', '2026-11-23',
     'closed', 'normal', 'Pia Moss', 'Renewal question resolved.'),
    ('sup-958', 'Crushed Carton Follow-up briefing', 'Acme', '2026-12-02',
     'active', 'low', 'Evan Ross', 'Briefing packet in review.');

WITH RECURSIVE sequence(value) AS (
    SELECT 1
    UNION ALL
    SELECT value + 1 FROM sequence WHERE value < 140
)
INSERT INTO cases
    (id, name, location, case_date, status, priority, owner, summary)
SELECT
    printf('sup-%04d', 2000 + value),
    CASE value % 7
        WHEN 0 THEN 'Missing accessory'
        WHEN 1 THEN 'Late delivery'
        WHEN 2 THEN 'Duplicate charge review'
        WHEN 3 THEN 'Damaged label'
        WHEN 4 THEN 'Address correction'
        WHEN 5 THEN 'Backorder inquiry'
        ELSE 'Packing slip mismatch'
    END,
    CASE value % 6
        WHEN 0 THEN 'Acme'
        WHEN 1 THEN 'Beacon'
        WHEN 2 THEN 'Cedar'
        WHEN 3 THEN 'Delta'
        WHEN 4 THEN 'Elm'
        ELSE 'Fulton'
    END,
    printf('2026-%02d-%02d', 1 + (value % 12), 1 + (value % 27)),
    CASE value % 4
        WHEN 0 THEN 'active'
        WHEN 1 THEN 'pending'
        WHEN 2 THEN 'closed'
        ELSE 'waiting'
    END,
    CASE value % 3
        WHEN 0 THEN 'high'
        WHEN 1 THEN 'normal'
        ELSE 'low'
    END,
    printf('Queue Owner %03d', value),
    printf('Routine support record %03d.', value)
FROM sequence;

INSERT INTO saved_profiles (location, escalation_channel, service_tier) VALUES
    ('Acme', 'regional-ops', 'gold'),
    ('Beacon', 'standard-queue', 'standard'),
    ('Cedar', 'regional-ops', 'silver'),
    ('Delta', 'standard-queue', 'standard');

INSERT INTO availability (team, shift_date, agents_available) VALUES
    ('carton-response', '2026-08-18', 4),
    ('billing-review', '2026-08-18', 2),
    ('returns', '2026-08-18', 3);
