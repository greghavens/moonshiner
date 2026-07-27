PRAGMA foreign_keys = ON;

CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(name, city)
);

CREATE TABLE availability (
    order_id TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (order_id, availability_date),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE reservations (
    id INTEGER PRIMARY KEY,
    order_id TEXT NOT NULL,
    reserved_for TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

INSERT INTO orders(id, name, city, status) VALUES
    ('ord-186', 'Lab glassware replenishment', 'Madison', 'approved'),
    ('ord-586', 'Conference lanyard order', 'Denver', 'draft'),
    ('ord-186-alt', 'Lab glassware replenishment sample', 'Denver', 'inactive'),
    ('ord-586-alt', 'Conference lanyard order', 'Madison', 'archived');

INSERT INTO availability(order_id, availability_date, available) VALUES
    ('ord-186', '2026-09-15', 1),
    ('ord-586', '2026-09-15', 0),
    ('ord-186-alt', '2026-09-15', 0),
    ('ord-586-alt', '2026-09-15', 1);
