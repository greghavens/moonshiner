PRAGMA foreign_keys = ON;

CREATE TABLE availability (
    item_name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (item_name, location, availability_date)
);

INSERT INTO availability
    (item_name, location, availability_date, available)
VALUES
    ('Renewal reminder', 'Volunteers', '2026-11-17', 0),
    ('Quarterly update', 'North Team', '2026-11-17', 1),
    ('Renewal reminder', 'North Team', '2026-11-17', 1),
    ('Renewal reminder', 'Volunteers', '2026-11-18', 1),
    ('Renewal reminders', 'Volunteers', '2026-11-17', 1),
    ('Quarterly update', 'Volunteers', '2026-11-17', 0),
    ('Quarterly update', 'North Team', '2026-11-18', 0),
    ('Quarterly Update', 'North Team', '2026-11-17', 0);

CREATE TABLE transient_failures (
    item_name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY (item_name, location, availability_date)
);

INSERT INTO transient_failures
    (item_name, location, availability_date, remaining)
VALUES
    ('Renewal reminder', 'Volunteers', '2026-11-17', 1);

CREATE TABLE operation_state (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    arguments_json TEXT NOT NULL,
    start_ns INTEGER NOT NULL,
    end_ns INTEGER,
    process_pid INTEGER NOT NULL,
    process_start_ticks TEXT NOT NULL,
    parent_pid INTEGER NOT NULL,
    parent_start_ticks TEXT NOT NULL,
    process_group_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    outcome TEXT
);
