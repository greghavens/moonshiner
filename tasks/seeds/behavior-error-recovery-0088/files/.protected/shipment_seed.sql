PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(name, city)
);

CREATE TABLE availability (
    shipment_id TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (shipment_id, availability_date),
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

CREATE TABLE reservations (
    id INTEGER PRIMARY KEY,
    shipment_id TEXT NOT NULL,
    reserved_for TEXT NOT NULL,
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

INSERT INTO shipments(id, name, city, status) VALUES
    ('shi-188', 'Museum loan crate', 'Baltimore', 'customs-review'),
    ('shi-588', 'Community garden seed packets', 'Tucson', 'in-transit'),
    ('shi-188-alt', 'Museum loan crate insurance copy', 'Annapolis', 'inactive'),
    ('shi-188-local', 'Museum loan crate', 'Annapolis', 'archived'),
    ('shi-588-alt', 'Community garden seed packets sample', 'Tucson', 'inactive'),
    ('shi-588-local', 'Community garden seed packets', 'Phoenix', 'archived');

INSERT INTO availability(shipment_id, availability_date, available) VALUES
    ('shi-188', '2026-09-16', 1),
    ('shi-588', '2026-09-16', 0),
    ('shi-188-alt', '2026-09-16', 0),
    ('shi-188-local', '2026-09-16', 0),
    ('shi-588-alt', '2026-09-16', 1),
    ('shi-588-local', '2026-09-16', 1);
