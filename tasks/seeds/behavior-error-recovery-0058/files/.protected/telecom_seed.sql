PRAGMA foreign_keys = ON;

CREATE TABLE plans (
    id TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    segment TEXT NOT NULL,
    status TEXT NOT NULL,
    service_class TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO plans
    (id, plan, segment, status, service_class, notes)
VALUES
    ('tel-158', 'Fiber plan', 'Family', 'active', 'fixed-broadband',
     'Family-segment fiber service used for September scheduling.'),
    ('tel-558', 'Tablet plan', 'Studio', 'active', 'mobile-data',
     'Studio-segment tablet service used for September scheduling.'),
    ('tel-658', 'Fiber plan', 'Studio', 'paused', 'fixed-broadband',
     'A separate segment retained for scope isolation.'),
    ('tel-858', 'Tablet plans', 'Family', 'draft', 'mobile-data',
     'A similarly named service retained for scope isolation.');

CREATE TABLE availability (
    plan TEXT NOT NULL,
    segment TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    daily_capacity INTEGER NOT NULL CHECK (daily_capacity >= 0),
    PRIMARY KEY (plan, segment, availability_date)
);

INSERT INTO availability
    (plan, segment, availability_date, available, daily_capacity)
VALUES
    ('Fiber plan', 'Family', '2026-09-13', 1, 12),
    ('Tablet plan', 'Studio', '2026-09-13', 0, 0),
    ('Fiber plan', 'Studio', '2026-09-13', 0, 0),
    ('Tablet plans', 'Family', '2026-09-20', 1, 5);
