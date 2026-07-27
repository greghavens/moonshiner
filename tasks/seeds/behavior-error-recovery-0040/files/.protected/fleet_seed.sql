PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    id TEXT PRIMARY KEY,
    vehicle_name TEXT NOT NULL,
    depot TEXT NOT NULL,
    status TEXT NOT NULL,
    vehicle_class TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO vehicles
    (id, vehicle_name, depot, status, vehicle_class, notes)
VALUES
    ('fle-140', 'Shuttle 30', 'Depot D', 'active', 'passenger shuttle',
     'Assigned to the Depot D service pool.'),
    ('fle-540', 'Van 12', 'Depot A', 'active', 'cargo van',
     'Assigned to the Depot A delivery pool.'),
    ('fle-640', 'Shuttle 30', 'Depot A', 'pending', 'passenger shuttle',
     'A separate vehicle retained for scope isolation.'),
    ('fle-840', 'Van 120', 'Depot D', 'retired', 'cargo van',
     'A similarly named historical vehicle.');

CREATE TABLE availability (
    vehicle_name TEXT NOT NULL,
    depot TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    remaining_capacity INTEGER NOT NULL CHECK (remaining_capacity >= 0),
    PRIMARY KEY (vehicle_name, depot, availability_date)
);

INSERT INTO availability
    (vehicle_name, depot, availability_date, available, remaining_capacity)
VALUES
    ('Shuttle 30', 'Depot D', '2026-11-13', 0, 0),
    ('Van 12', 'Depot A', '2026-11-13', 1, 3),
    ('Shuttle 30', 'Depot A', '2026-11-13', 1, 8),
    ('Van 120', 'Depot D', '2026-11-13', 0, 0);
