PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived'))
);

INSERT INTO accounts (stable_id, name, region, status, lifecycle) VALUES
    ('crm-129', 'Arbor Foods 029', 'West', 'active', 'current'),
    ('crm-529', 'Bright Dental 029', 'Central', 'pending', 'current'),
    ('crm-829', 'Arbor Foods 029 Archive', 'West', 'closed', 'archived');
