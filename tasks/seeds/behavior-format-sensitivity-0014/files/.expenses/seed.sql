PRAGMA foreign_keys = ON;

CREATE TABLE expense_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO expense_records (stable_id, name, location, status) VALUES
    ('exp-114', 'Train fare', 'Chicago', 'active'),
    ('exp-514', 'Team lunch', 'Boston', 'pending'),
    ('exp-914', 'Train fare', 'Boston', 'closed');
