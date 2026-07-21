PRAGMA foreign_keys = ON;

CREATE TABLE work_orders (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES work_orders(stable_id),
    message TEXT NOT NULL
);

INSERT INTO work_orders
    (stable_id, name, location, status, priority, scheduled_for, lifecycle)
VALUES
    ('fac-197', 'Atrium lighting repair', 'Building A', 'assigned', 'high', '2026-07-23', 'current'),
    ('fac-597', 'Training room setup', 'Building B', 'queued', 'normal', '2026-07-24', 'current'),
    ('fac-108', 'Atrium lighting repair archive', 'Building A', 'closed', 'low', '2025-09-08', 'current'),
    ('fac-219', 'Atrium lighting repair', 'Building C', 'pending', 'normal', '2026-07-26', 'current'),
    ('fac-330', 'Atrium lighting repairs', 'Building A', 'complete', 'normal', '2026-07-18', 'current'),
    ('fac-441', 'Atrium lighting repair', 'Building A', 'closed', 'normal', '2025-08-11', 'archived'),
    ('fac-628', 'Training room setups', 'Building B', 'assigned', 'normal', '2026-07-25', 'current'),
    ('fac-739', 'Training room setup', 'Building C', 'complete', 'low', '2026-07-19', 'current'),
    ('fac-840', 'Training room setup assessment', 'Building B', 'scheduled', 'normal', '2026-07-22', 'current'),
    ('fac-951', 'Training room setup', 'Building B', 'closed', 'normal', '2025-10-14', 'archived'),
    ('fac-062', 'Loading dock inspection', 'Building D', 'requested', 'high', '2026-07-27', 'current');
