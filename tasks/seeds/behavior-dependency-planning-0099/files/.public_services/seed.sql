PRAGMA foreign_keys = ON;

CREATE TABLE records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    service_type TEXT NOT NULL,
    submitted_date TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO records
    (stable_id, name, location, status, service_type, submitted_date, lifecycle)
VALUES
    ('pub-199', 'Pet license — Milo', 'Aurora', 'approved', 'animal-license', '2026-06-12', 'current'),
    ('pub-599', 'Community hall permit', 'Lakewood', 'submitted', 'facility-permit', '2026-07-08', 'current'),
    ('pub-109', 'Pet license — Milo archive', 'Aurora', 'closed', 'animal-license', '2025-05-04', 'current'),
    ('pub-219', 'Pet license — Milo', 'Denver', 'pending', 'animal-license', '2026-06-18', 'current'),
    ('pub-330', 'Pet licence — Milo', 'Aurora', 'approved', 'animal-license', '2026-06-20', 'current'),
    ('pub-441', 'Pet license — Milo', 'Aurora', 'expired', 'animal-license', '2025-01-15', 'archived'),
    ('pub-628', 'Community hall permits', 'Lakewood', 'approved', 'facility-permit', '2026-06-29', 'current'),
    ('pub-739', 'Community hall permit', 'Wheat Ridge', 'approved', 'facility-permit', '2026-07-01', 'current'),
    ('pub-840', 'Community hall permit assessment', 'Lakewood', 'requested', 'consultation', '2026-07-09', 'current'),
    ('pub-951', 'Community hall permit', 'Lakewood', 'closed', 'facility-permit', '2025-09-17', 'archived'),
    ('pub-062', 'Block party permit', 'Aurora', 'approved', 'street-event', '2026-07-11', 'current');
