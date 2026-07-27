PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    trip_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived'))
);

CREATE TABLE availability_faults (
    stable_id TEXT PRIMARY KEY REFERENCES trips(stable_id),
    failures_remaining INTEGER NOT NULL CHECK (failures_remaining >= 0),
    attempts INTEGER NOT NULL CHECK (attempts >= 0)
);

INSERT INTO trips
    (stable_id, name, location, trip_date, available, lifecycle)
VALUES
    ('tra-202', 'Halifax conference trip', 'Halifax', '2026-10-15', 1, 'current'),
    ('tra-602', 'Santa Fe field visit', 'Santa Fe', '2026-10-15', 1, 'current'),
    ('tra-202-alt', 'Halifax conference trip receipt', 'Toronto', '2026-10-15', 0, 'current'),
    ('tra-314', 'Halifax conference trip', 'Halifax', '2026-10-14', 0, 'current'),
    ('tra-415', 'Halifax conference trip', 'Dartmouth', '2026-10-15', 0, 'current'),
    ('tra-516', 'Santa Fe field visit notes', 'Santa Fe', '2026-10-15', 0, 'current'),
    ('tra-617', 'Santa Fe field visit', 'Albuquerque', '2026-10-15', 0, 'current'),
    ('tra-718', 'Santa Fe field visit', 'Santa Fe', '2026-10-15', 0, 'archived');

INSERT INTO availability_faults
    (stable_id, failures_remaining, attempts)
VALUES
    ('tra-202', 1, 0),
    ('tra-602', 0, 0);
