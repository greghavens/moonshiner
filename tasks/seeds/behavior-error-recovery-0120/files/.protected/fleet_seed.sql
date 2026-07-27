PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    depot TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(name, depot)
);

CREATE TABLE availability (
    vehicle_id TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (vehicle_id, availability_date),
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

CREATE TABLE vehicle_notes (
    vehicle_id TEXT PRIMARY KEY,
    service_note TEXT NOT NULL,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

INSERT INTO vehicles(id, name, depot, status) VALUES
    ('fle-220', 'Shuttle 30 120', 'Depot D', 'active'),
    ('fle-620', 'Van 12 120', 'Depot A', 'pending'),
    ('fle-320', 'Shuttle 30 120', 'Depot A', 'active'),
    ('fle-720', 'Van 12 120', 'Depot D', 'active');

INSERT INTO availability(vehicle_id, availability_date, available) VALUES
    ('fle-220', '2026-11-21', 1),
    ('fle-620', '2026-11-21', 0),
    ('fle-320', '2026-11-21', 0),
    ('fle-720', '2026-11-21', 1);

INSERT INTO vehicle_notes(vehicle_id, service_note) VALUES
    ('fle-220', 'inspection current'),
    ('fle-620', 'awaiting dispatch review'),
    ('fle-320', 'assigned to reserve pool'),
    ('fle-720', 'inspection current');
