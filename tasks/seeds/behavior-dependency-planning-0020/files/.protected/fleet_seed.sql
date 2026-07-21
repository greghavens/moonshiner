PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    vehicle_class TEXT NOT NULL,
    route_note TEXT NOT NULL
);

INSERT INTO vehicles
    (id, name, location, status, record_date, vehicle_class, route_note)
VALUES
    ('fle-120', 'Shuttle 30', 'Depot D', 'maintenance-due', '2026-08-11',
     'passenger shuttle', 'Inspection required before route assignment.'),
    ('fle-520', 'Cargo Van 12', 'Depot A', 'available', '2026-08-12',
     'cargo van', 'Cleared for standard dispatch.'),
    ('fle-920', 'Shuttle 30', 'Depot B', 'retired', '2025-08-11',
     'passenger shuttle', 'Historical record retained for audit.'),
    ('fle-820', 'Cargo Van 12', 'Depot A Annex', 'retired', '2025-08-12',
     'cargo van', 'Historical annex record retained for audit.');

CREATE TABLE saved_preferences (
    dispatcher TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (dispatcher, preference)
VALUES ('dispatch-lead', 'compact');

CREATE TABLE availability (
    vehicle_id TEXT NOT NULL,
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL,
    PRIMARY KEY (vehicle_id, service_date),
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

INSERT INTO availability (vehicle_id, service_date, available)
VALUES
    ('fle-120', '2026-08-13', 0),
    ('fle-520', '2026-08-13', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
