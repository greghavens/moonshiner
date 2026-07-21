CREATE TABLE accounts (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    is_stale INTEGER NOT NULL CHECK (is_stale IN (0, 1))
);

INSERT INTO accounts (stable_id, name, location, status, is_stale) VALUES
    ('acct-0f4a91c7', 'Indigo Travel Cooperative', 'International', 'Active', 0),
    ('acct-792eb4d0', 'Indigo Travel Cooperative', 'International', 'Archived', 1),
    ('acct-8b2d66e3', 'Juniper Neighborhood Market', 'Southwest Region', 'Pending Review', 0),
    ('acct-5297cb1a', 'Juniper Neighborhood Market', 'Southwest Region', 'Closed', 1),
    ('acct-24f191a6', 'Indigo Travel Cooperative', 'Northwest Region', 'Active', 0),
    ('acct-b9192e18', 'Juniper Neighborhood Markets', 'Southwest Region', 'Active', 0),
    ('acct-65d203cc', 'Saffron Field Services', 'International', 'Active', 0);
