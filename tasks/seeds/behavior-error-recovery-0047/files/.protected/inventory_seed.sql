PRAGMA foreign_keys = ON;

CREATE TABLE items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancelled', 'pending', 'closed')
    ),
    cancellation_reason TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

CREATE TABLE cancellation_attempts (
    item_id TEXT PRIMARY KEY REFERENCES items(id),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id),
    message TEXT NOT NULL
);

INSERT INTO items
    (id, name, location, status, cancellation_reason, revision)
VALUES
    ('inv-147', 'Packing tape', 'Warehouse C', 'active', NULL, 1),
    ('inv-547', 'Printer paper', 'Warehouse D', 'pending', NULL, 2),
    ('inv-647', 'Packing tape', 'Warehouse D', 'closed', NULL, 4),
    ('inv-947', 'Pallet wrap', 'Warehouse A', 'active', NULL, 3);
