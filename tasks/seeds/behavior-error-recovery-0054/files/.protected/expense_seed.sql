PRAGMA foreign_keys = ON;

CREATE TABLE expense_items (
    id TEXT PRIMARY KEY,
    item_name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    ledger_group TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO expense_items
    (id, item_name, location, status, ledger_group, notes)
VALUES
    ('exp-154', 'Train fare', 'Chicago', 'active', 'travel',
     'Rail reimbursement packet for the Chicago planning window.'),
    ('exp-554', 'Team lunch', 'Boston', 'pending', 'meals',
     'Team meal packet for the Boston planning window.'),
    ('exp-654', 'Train fare', 'Boston', 'pending', 'travel',
     'A separate Boston rail packet retained for scope isolation.'),
    ('exp-854', 'Team lunches', 'Chicago', 'draft', 'meals',
     'A similarly named draft retained for scope isolation.');

CREATE TABLE availability (
    item_name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    packet_capacity INTEGER NOT NULL CHECK (packet_capacity >= 0),
    PRIMARY KEY (item_name, location, availability_date)
);

INSERT INTO availability
    (item_name, location, availability_date, available, packet_capacity)
VALUES
    ('Train fare', 'Chicago', '2026-09-27', 0, 0),
    ('Team lunch', 'Boston', '2026-09-27', 1, 12),
    ('Train fare', 'Boston', '2026-09-27', 1, 4),
    ('Team lunches', 'Chicago', '2026-10-04', 1, 6);
