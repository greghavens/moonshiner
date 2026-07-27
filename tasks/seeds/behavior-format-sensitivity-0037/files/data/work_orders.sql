PRAGMA foreign_keys = ON;

CREATE TABLE work_orders (
    id TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    summary TEXT NOT NULL,
    priority TEXT NOT NULL,
    assigned_team TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO work_orders
    (id, site, summary, priority, assigned_team, opened_at, status)
VALUES
    ('fac-137', 'Aurora Distribution Annex', 'Dock leveler 3 hydraulic leak', 'urgent', 'Facilities Mechanical', '2026-07-22T15:40:00-06:00', 'open'),
    ('fac-537', 'Juniper Research Wing', 'Replace conference room occupancy sensor', 'routine', 'Building Controls', '2026-07-21T09:15:00-06:00', 'open'),
    ('fac-317', 'Mesa Operations Center', 'Inspect intermittent generator alarm', 'high', 'Critical Systems', '2026-07-20T18:05:00-06:00', 'in_progress'),
    ('fac-137-archive', 'Aurora Distribution Annex', 'Dock leveler 3 hinge inspection', 'routine', 'Facilities Mechanical', '2025-11-03T10:30:00-07:00', 'closed');
