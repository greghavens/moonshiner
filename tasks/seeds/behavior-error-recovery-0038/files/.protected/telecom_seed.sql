PRAGMA foreign_keys = ON;

CREATE TABLE plans (
    id TEXT PRIMARY KEY,
    plan_name TEXT NOT NULL,
    account TEXT NOT NULL,
    status TEXT NOT NULL,
    service_tier TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO plans
    (id, plan_name, account, status, service_tier, notes)
VALUES
    ('tel-138', 'Fiber plan', 'Family', 'active', 'residential',
     'Primary household fiber service.'),
    ('tel-538', 'Tablet plan', 'Studio', 'pending', 'mobile-data',
     'Studio tablet line awaiting its activation window.'),
    ('tel-638', 'Fiber plan', 'Studio', 'pending', 'business',
     'Separate studio fiber service.'),
    ('tel-739', 'Tablet plan', 'Family', 'active', 'mobile-data',
     'Separate household tablet line.');

CREATE TABLE availability (
    plan_name TEXT NOT NULL,
    account TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    capacity_remaining INTEGER NOT NULL CHECK (capacity_remaining >= 0),
    PRIMARY KEY (plan_name, account, availability_date)
);

INSERT INTO availability
    (plan_name, account, availability_date, available, capacity_remaining)
VALUES
    ('Fiber plan', 'Family', '2026-09-11', 1, 6),
    ('Tablet plan', 'Studio', '2026-09-11', 0, 0),
    ('Fiber plan', 'Studio', '2026-09-11', 1, 2),
    ('Tablet plan', 'Family', '2026-09-18', 1, 5);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    detail TEXT NOT NULL
);
