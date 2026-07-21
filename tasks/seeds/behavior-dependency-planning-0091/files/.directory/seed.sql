PRAGMA foreign_keys = ON;

CREATE TABLE employees (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES employees(stable_id),
    message TEXT NOT NULL
);

INSERT INTO employees (stable_id, name, department, status, lifecycle) VALUES
    ('emp-4f91', 'Priya Nair', 'Analytics', 'Active', 'current'),
    ('emp-8c27', 'Mateo Silva', 'Customer Success', 'On leave', 'current'),
    ('emp-1a42', 'Priya Nair', 'Finance', 'Active', 'current'),
    ('emp-2b63', 'Priya Nayar', 'Analytics', 'Active', 'current'),
    ('emp-3d74', 'Priya Nair', 'Analytics', 'Former', 'stale'),
    ('emp-5e18', 'Mateo Silva', 'Customer Support', 'Active', 'current'),
    ('emp-6f29', 'Matteo Silva', 'Customer Success', 'Active', 'current'),
    ('emp-7a30', 'Mateo Silva', 'Customer Success', 'Former', 'stale'),
    ('emp-9b51', 'Avery Chen', 'Analytics', 'Active', 'current'),
    ('emp-ac62', 'Jordan Okafor', 'Customer Success', 'Active', 'current');
