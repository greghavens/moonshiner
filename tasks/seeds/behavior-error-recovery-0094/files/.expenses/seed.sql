PRAGMA foreign_keys = ON;

CREATE TABLE expense_availability (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale'))
);

CREATE TABLE transient_failures (
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    failures_remaining INTEGER NOT NULL CHECK (failures_remaining >= 0),
    PRIMARY KEY (name, location, availability_date)
);

CREATE TABLE availability_attempts (
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
    PRIMARY KEY (name, location, availability_date)
);

INSERT INTO expense_availability
    (stable_id, name, location, availability_date, available, lifecycle)
VALUES
    ('exp-194', 'Denver lodging — policy summit', 'Denver', '2026-09-04', 0, 'current'),
    ('exp-594', 'Tucson mileage — field sampling', 'Tucson', '2026-09-04', 1, 'current'),
    ('exp-294', 'Denver lodging — policy summit', 'Denver', '2026-09-03', 1, 'current'),
    ('exp-394', 'Denver lodging — policy summit', 'Planning', '2026-09-04', 1, 'current'),
    ('exp-494', 'Denver lodging — policy summit estimate', 'Denver', '2026-09-04', 1, 'current'),
    ('exp-694', 'Tucson mileage — field sampling', 'Tucson', '2026-09-05', 0, 'current'),
    ('exp-794', 'Tucson mileage — field sampling', 'Phoenix', '2026-09-04', 0, 'current'),
    ('exp-894', 'Tucson mileage — field sampling', 'Tucson', '2026-09-04', 0, 'stale');

INSERT INTO transient_failures
    (name, location, availability_date, failures_remaining)
VALUES
    ('Denver lodging — policy summit', 'Denver', '2026-09-04', 1);
