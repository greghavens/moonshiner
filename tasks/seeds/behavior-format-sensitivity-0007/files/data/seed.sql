PRAGMA foreign_keys = ON;

CREATE TABLE items (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'closed'))
);

CREATE TABLE saved_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES items(stable_id)
);

INSERT INTO items (stable_id, name, location, status) VALUES
    ('inv-107', 'Packing tape', 'Warehouse C', 'active'),
    ('inv-507', 'Printer paper', 'Warehouse D', 'pending'),
    ('inv-907', 'Packing tape', 'Warehouse D', 'closed');

INSERT INTO saved_preferences (key, value) VALUES
    ('default_location', 'Warehouse C'),
    ('result_order', 'stable_id');
