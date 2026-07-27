PRAGMA foreign_keys = ON;

CREATE TABLE expense_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES expense_records(stable_id)
);

CREATE TABLE profile (
    profile_key TEXT PRIMARY KEY,
    profile_value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT PRIMARY KEY,
    available INTEGER NOT NULL CHECK (available IN (0, 1))
);

INSERT INTO expense_records
    (stable_id, name, location, status, lifecycle)
VALUES
    ('exp-254', 'Community printing invoice', 'Seattle', 'approved', 'current'),
    ('exp-654', 'Mentor breakfast receipt', 'Chicago', 'submitted', 'current'),
    ('exp-1054', 'Community printing invoice archive', 'Boston', 'closed', 'archived');

INSERT INTO profile (profile_key, profile_value)
VALUES ('review_queue', 'community-finance');

INSERT INTO availability (location, available)
VALUES ('Seattle', 1), ('Chicago', 1), ('Boston', 0);
