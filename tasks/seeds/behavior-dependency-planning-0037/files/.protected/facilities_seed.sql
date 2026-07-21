PRAGMA foreign_keys = ON;

CREATE TABLE facilities_requests (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO facilities_requests
    (id, name, location, requested_date, status, priority, coordinator, notes)
VALUES
    ('fac-407', 'Loading Dock Door Inspection', 'Warehouse C',
     '2026-07-18', 'scheduled', 'standard', 'Rina Holt',
     'Current inspection request for the maintenance handoff.'),
    ('fac-862', 'Quiet Room Ventilation Check', 'Building D',
     '2026-07-20', 'in-progress', 'standard', 'Owen Park',
     'Current ventilation request assigned to building maintenance.'),
    ('fac-119', 'Loading Dock Door Inspection', 'Warehouse C Annex',
     '2025-07-11', 'archived', 'standard', 'Rina Holt',
     'Historical inspection at the annex loading area.'),
    ('fac-238', 'Loading Dock Door Inspection - 2025', 'Warehouse C',
     '2025-07-13', 'archived', 'standard', 'Milo West',
     'Historical similarly named warehouse request.'),
    ('fac-351', 'Quiet Room Ventilation Check', 'Building D South',
     '2025-06-08', 'archived', 'standard', 'Owen Park',
     'Historical ventilation request from another location.'),
    ('fac-574', 'Quiet Room Ventilation Check (Historical)', 'Building D',
     '2025-06-10', 'archived', 'standard', 'Nia Cole',
     'Historical similarly named building request.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('maintenance-handoff', 'show-open-requests');

CREATE TABLE availability (
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    technician_slots INTEGER NOT NULL,
    PRIMARY KEY (location, service_date)
);

INSERT INTO availability (location, service_date, technician_slots)
VALUES
    ('Warehouse C', '2026-07-22', 2),
    ('Building D', '2026-07-22', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    action_id TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
