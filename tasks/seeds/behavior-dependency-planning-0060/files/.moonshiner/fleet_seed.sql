PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    service_class TEXT NOT NULL,
    custodian TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

CREATE UNIQUE INDEX vehicles_name_location_id
    ON vehicles(name, location, stable_id);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO vehicles
    (stable_id, name, location, status, service_class, custodian, internal_note)
VALUES
    ('veh_2d924c90', 'Box Truck 18', 'Warehouse Fleet',
     'Ready for service', 'medium-duty', 'Logistics Operations',
     'Route equipment assignment is held in the dispatch system.'),
    ('veh_a78113f6', 'Passenger Van 23', 'Programs Fleet',
     'Scheduled maintenance', 'passenger', 'Community Programs',
     'Maintenance appointment details are held by Fleet Services.'),
    ('veh_c066db41', 'Box Truck 18', 'Overflow Lot',
     'Out of service', 'medium-duty', 'Logistics Operations',
     'Historical location record retained for audit.'),
    ('veh_e3ad6bb7', 'Passenger Van 23', 'Warehouse Fleet',
     'Ready for service', 'passenger', 'Logistics Operations',
     'Separate vehicle assignment.'),
    ('veh_305b50c2', 'Passenger Van 32', 'Programs Fleet',
     'Ready for service', 'passenger', 'Community Programs',
     'Separate program vehicle.'),
    ('veh_f4179a02', 'Box Truck 81', 'Warehouse Fleet',
     'Inspection pending', 'medium-duty', 'Logistics Operations',
     'Separate warehouse vehicle.');

INSERT INTO saved_preferences (preference_key, preference_value)
VALUES
    ('default_location', 'Warehouse Fleet'),
    ('include_archived', 'false');

PRAGMA user_version = 1;
