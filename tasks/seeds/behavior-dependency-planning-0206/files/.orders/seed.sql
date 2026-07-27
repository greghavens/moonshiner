PRAGMA foreign_keys = ON;

CREATE TABLE order_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT,
    order_date TEXT,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'archived')),
    quantity INTEGER NOT NULL,
    requester TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES order_records(stable_id)
);

INSERT INTO order_records
    (stable_id, name, city, status, order_date, lifecycle, quantity, requester, notes)
VALUES
    ('ord-206', 'Ergonomic chair order', 'Boise', 'processing',
     '2026-07-28', 'active', 14, 'Morgan Patel',
     'Mesh-backed chairs for the second-floor workspace.'),
    ('ord-606', 'Volunteer appreciation kits', 'Phoenix', 'confirmed',
     NULL, 'active', 80, 'Casey Nguyen',
     'Reusable bottles, thank-you cards, and tote bags.'),
    ('ord-1006', 'Ergonomic chair order', 'Boise', 'completed',
     '2025-11-14', 'archived', 8, 'Records Team',
     'Prior-year order retained for audit.'),
    ('ord-1406', 'Ergonomic chair order', 'Meridian', 'queued',
     '2026-08-02', 'active', 6, 'Taylor Reed',
     'Satellite office furniture request.'),
    ('ord-1806', 'Ergonomic chair order - footrests', 'Boise', 'processing',
     '2026-07-29', 'active', 14, 'Morgan Patel',
     'Related accessories; separate order.'),
    ('ord-2206', 'Volunteer appreciation kits', 'Tempe', 'ready',
     '2026-07-26', 'active', 40, 'Avery Diaz',
     'Separate city program.'),
    ('ord-2606', 'Volunteer appreciation kits archive', 'Phoenix', 'completed',
     '2025-07-20', 'archived', 60, 'Records Team',
     'Archived prior event order.');

INSERT INTO notifications (stable_id, message)
VALUES ('ord-1006', 'Archived order retained under the standard schedule.');
