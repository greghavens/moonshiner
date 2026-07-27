PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    depot TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    UNIQUE (name, depot, status)
);

CREATE TABLE availability (
    stable_id TEXT NOT NULL REFERENCES vehicles(stable_id),
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (stable_id, service_date)
);

CREATE TABLE transient_rules (
    stable_id TEXT NOT NULL REFERENCES vehicles(stable_id),
    service_date TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL CHECK (failed_attempts >= 0),
    PRIMARY KEY (stable_id, service_date)
);

CREATE TABLE saved_preferences (
    owner TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    PRIMARY KEY (owner, preference_key)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES vehicles(stable_id),
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO vehicles (stable_id, name, depot, status) VALUES
    ('veh-truck-008', 'Truck 8 garden delivery', 'Depot B', 'active'),
    ('veh-sedan-004', 'Sedan 4 clinic courier', 'Depot C', 'active'),
    ('veh-truck-108', 'Truck 8 garden delivery archive', 'Depot B', 'cancelled'),
    ('veh-sedan-204', 'Sedan 4 clinic courier', 'Depot B', 'active'),
    ('veh-truck-308', 'Truck 8 garden delivery', 'Depot C', 'active');

INSERT INTO availability (stable_id, service_date, available) VALUES
    ('veh-truck-008', '2026-09-14', 1),
    ('veh-truck-008', '2026-09-21', 0),
    ('veh-sedan-004', '2026-09-14', 0),
    ('veh-sedan-004', '2026-09-21', 1),
    ('veh-sedan-204', '2026-09-14', 1),
    ('veh-truck-308', '2026-09-14', 0);

INSERT INTO transient_rules (stable_id, service_date, failed_attempts) VALUES
    ('veh-sedan-004', '2026-09-14', 1);

INSERT INTO saved_preferences (owner, preference_key, preference_value) VALUES
    ('fleet-coordinator', 'display_timezone', 'America/Denver');
