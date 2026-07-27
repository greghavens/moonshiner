PRAGMA foreign_keys = ON;

CREATE TABLE claim_availability (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    office TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale'))
);

CREATE TABLE transient_failures (
    name TEXT NOT NULL,
    office TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    failures_remaining INTEGER NOT NULL CHECK (failures_remaining >= 0),
    PRIMARY KEY (name, office, availability_date)
);

CREATE TABLE availability_attempts (
    name TEXT NOT NULL,
    office TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
    PRIMARY KEY (name, office, availability_date)
);

INSERT INTO claim_availability
    (stable_id, name, office, availability_date, available, lifecycle)
VALUES
    ('clm-096', 'Water damage claim — archive room', 'Central Office', '2026-09-11', 1, 'current'),
    ('clm-596', 'Lost baggage claim — conference trip', 'Travel Desk', '2026-09-11', 0, 'current'),
    ('clm-196', 'Water damage claim — archive room', 'Central Office', '2026-09-10', 0, 'current'),
    ('clm-296', 'Water damage claim — archive room', 'North Office', '2026-09-11', 0, 'current'),
    ('clm-396', 'Water damage claim — archive rooms', 'Central Office', '2026-09-11', 0, 'current'),
    ('clm-696', 'Lost baggage claim — conference trip', 'Travel Desk', '2026-09-12', 1, 'current'),
    ('clm-796', 'Lost baggage claim — conference trip', 'Claims Desk', '2026-09-11', 1, 'current'),
    ('clm-896', 'Lost baggage claim — conference travel', 'Travel Desk', '2026-09-11', 1, 'current'),
    ('clm-996', 'Lost baggage claim — conference trip', 'Travel Desk', '2026-09-11', 1, 'stale');

INSERT INTO transient_failures
    (name, office, availability_date, failures_remaining)
VALUES
    ('Water damage claim — archive room', 'Central Office', '2026-09-11', 1);
