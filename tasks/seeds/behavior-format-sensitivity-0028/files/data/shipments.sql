PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    current INTEGER NOT NULL CHECK (current IN (0, 1))
);

INSERT INTO shipments (id, name, location, status, current) VALUES
    ('shi-128', 'Parcel Drift 028', 'Portland', 'active', 1),
    ('shi-528', 'Parcel Aurora 028', 'Denver', 'pending', 1),
    ('shi-218', 'Parcel Harbor 028', 'Tacoma', 'delivered', 1),
    ('shi-128-archive', 'Parcel Drift archive', 'Salem', 'archived', 0);
