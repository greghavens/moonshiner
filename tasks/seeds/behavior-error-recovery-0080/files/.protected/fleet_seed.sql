PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    depot TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    UNIQUE (name, depot)
);

CREATE TABLE availability (
    vehicle_name TEXT NOT NULL,
    depot TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (vehicle_name, depot, availability_date),
    FOREIGN KEY (vehicle_name, depot) REFERENCES vehicles(name, depot)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY,
    vehicle_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

INSERT INTO vehicles(id, name, depot, status) VALUES
    ('fle-180', 'Shuttle 30 accessibility run', 'Depot D', 'active'),
    ('fle-580', 'Van 12 library route', 'Depot A', 'active'),
    ('fle-180-alt', 'Shuttle 30 accessibility run inspection', 'Service Bay', 'cancelled'),
    ('fle-580-alt', 'Van 12 library route draft', 'Depot A', 'cancelled');

INSERT INTO availability(vehicle_name, depot, availability_date, available) VALUES
    ('Shuttle 30 accessibility run', 'Depot D', '2026-08-10', 1),
    ('Van 12 library route', 'Depot A', '2026-08-10', 0),
    ('Shuttle 30 accessibility run inspection', 'Service Bay', '2026-08-10', 0),
    ('Van 12 library route draft', 'Depot A', '2026-08-10', 1);
