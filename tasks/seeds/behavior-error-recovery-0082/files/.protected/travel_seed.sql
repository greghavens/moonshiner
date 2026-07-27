PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'booked', 'inactive', 'cancelled')),
    UNIQUE (name, city)
);

CREATE TABLE availability (
    trip_name TEXT NOT NULL,
    city TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (trip_name, city, availability_date),
    FOREIGN KEY (trip_name, city) REFERENCES trips(name, city)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY,
    trip_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (trip_id) REFERENCES trips(id)
);

INSERT INTO trips(id, name, city, status) VALUES
    ('tra-182', 'Kyoto archives visit', 'Kyoto', 'booked'),
    ('tra-582', 'Osaka supplier tour', 'Osaka', 'proposed'),
    ('tra-182-alt', 'Kyoto archives visit draft', 'Tokyo', 'inactive'),
    ('tra-582-alt', 'Osaka supplier tour review', 'Kyoto', 'cancelled');

INSERT INTO availability(trip_name, city, availability_date, available) VALUES
    ('Kyoto archives visit', 'Kyoto', '2026-09-24', 0),
    ('Osaka supplier tour', 'Osaka', '2026-09-24', 0),
    ('Kyoto archives visit draft', 'Tokyo', '2026-09-24', 1),
    ('Osaka supplier tour review', 'Kyoto', '2026-09-24', 1);
