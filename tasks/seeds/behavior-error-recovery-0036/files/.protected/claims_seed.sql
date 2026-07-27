PRAGMA foreign_keys = ON;

CREATE TABLE claim_items (
    id TEXT PRIMARY KEY,
    item_name TEXT NOT NULL,
    office TEXT NOT NULL,
    status TEXT NOT NULL,
    queue_name TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO claim_items
    (id, item_name, office, status, queue_name, notes)
VALUES
    ('clm-136', 'Theft claim', 'West Office', 'open', 'property',
     'West Office intake category retained for the planning calendar.'),
    ('clm-536', 'Windshield claim', 'North Office', 'open', 'auto-glass',
     'North Office glass category retained for the planning calendar.'),
    ('clm-636', 'Theft claim', 'North Office', 'review', 'property',
     'A different office entry retained for exact-match isolation.'),
    ('clm-836', 'Windshield claims', 'West Office', 'draft', 'auto-glass',
     'A similarly named entry retained for exact-match isolation.');

CREATE TABLE availability (
    item_name TEXT NOT NULL,
    office TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    intake_capacity INTEGER NOT NULL CHECK (intake_capacity >= 0),
    PRIMARY KEY (item_name, office, availability_date)
);

INSERT INTO availability
    (item_name, office, availability_date, available, intake_capacity)
VALUES
    ('Theft claim', 'West Office', '2026-11-27', 1, 6),
    ('Windshield claim', 'North Office', '2026-11-27', 0, 0),
    ('Theft claim', 'North Office', '2026-11-27', 0, 0),
    ('Windshield claims', 'West Office', '2026-12-04', 1, 4);
