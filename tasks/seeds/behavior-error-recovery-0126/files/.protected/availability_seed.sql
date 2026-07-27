PRAGMA foreign_keys = ON;

CREATE TABLE availability (
    order_name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (order_name, location, availability_date)
);

INSERT INTO availability
    (order_name, location, availability_date, available)
VALUES
    ('Office order 126', 'Boise', '2026-09-27', 0),
    ('Gift order 126', 'Phoenix', '2026-09-27', 1),
    ('Office order 126', 'Phoenix', '2026-09-27', 1),
    ('Office order 126', 'Boise', '2026-09-26', 1),
    ('Office orders 126', 'Boise', '2026-09-27', 1),
    ('Gift order 126', 'Boise', '2026-09-27', 0),
    ('Gift order 126', 'Phoenix', '2026-09-28', 0),
    ('Gift Order 126', 'Phoenix', '2026-09-27', 0);

CREATE TABLE transient_failures (
    order_name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY (order_name, location, availability_date)
);

INSERT INTO transient_failures
    (order_name, location, availability_date, remaining)
VALUES
    ('Gift order 126', 'Phoenix', '2026-09-27', 1);

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
