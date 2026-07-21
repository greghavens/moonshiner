PRAGMA foreign_keys = ON;

CREATE TABLE order_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    ordered_on TEXT NOT NULL,
    vendor TEXT NOT NULL,
    description TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES order_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO order_records
    (stable_id, name, location, status, ordered_on, vendor, description, lifecycle)
VALUES
    ('com-206', 'Ergonomic chair order', 'Boise', 'processing', '2026-07-15', 'High Desert Office Supply', 'Workspace seating order is being prepared.', 'current'),
    ('com-606', 'Welcome-kit order', 'Phoenix', 'backordered', '2026-07-17', 'Sonoran Onboarding Goods', 'New-hire welcome kits are awaiting inventory.', 'current'),
    ('com-1006', 'Ergonomic chair order archive', 'Madison', 'closed', '2025-07-15', 'Lakeside Contract Furnishings', 'Closed archive for a similarly named order.', 'archived');
