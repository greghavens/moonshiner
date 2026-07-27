PRAGMA foreign_keys = ON;

CREATE TABLE fleet (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO fleet (id, name, location, status)
VALUES
    ('fle-120', 'Shuttle 30', 'Depot D', 'active'),
    ('fle-520', 'Van 12', 'Depot A', 'pending'),
    ('fle-620', 'Shuttle 30', 'Depot A', 'pending');

CREATE TABLE availability (
    fleet_id TEXT NOT NULL,
    available_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (fleet_id, available_date),
    FOREIGN KEY (fleet_id) REFERENCES fleet(id)
);

INSERT INTO availability (fleet_id, available_date, available)
VALUES
    ('fle-120', '2026-11-11', 0),
    ('fle-520', '2026-11-11', 1),
    ('fle-620', '2026-11-11', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    fleet_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
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
    outcome TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
