PRAGMA foreign_keys = ON;

CREATE TABLE library_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    branch TEXT NOT NULL,
    status TEXT NOT NULL,
    shelf TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE status_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES library_records(stable_id),
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES library_records(stable_id),
    recipient TEXT NOT NULL,
    outcome TEXT NOT NULL,
    change_id INTEGER NOT NULL REFERENCES status_changes(change_id)
);

INSERT INTO library_records
    (stable_id, name, branch, status, shelf, lifecycle)
VALUES
    ('lib-273', 'The Quiet Observatory', 'Central Branch', 'available', 'QB-17', 'current'),
    ('lib-673', 'Cooking with Winter Roots', 'East Branch', 'on-loan', 'CK-42', 'current'),
    ('lib-1073', 'The Quiet Observatory archive', 'Riverside Branch', 'closed', 'AR-03', 'current'),
    ('lib-184', 'The Quiet Observatory', 'East Branch', 'available', 'QB-22', 'current'),
    ('lib-295', 'The Quiet Observatories', 'Central Branch', 'available', 'QB-18', 'current'),
    ('lib-406', 'The Quiet Observatory', 'Central Branch', 'withdrawn', 'AR-11', 'archived'),
    ('lib-517', 'Cooking with Winter Roots', 'Central Branch', 'available', 'CK-40', 'current'),
    ('lib-628', 'Cooking with Winter Root', 'East Branch', 'available', 'CK-41', 'current'),
    ('lib-739', 'Cooking with Winter Roots', 'East Branch', 'returned', 'AR-27', 'archived'),
    ('lib-840', 'Cooking with Winter Roots companion', 'East Branch', 'available', 'CK-43', 'current'),
    ('lib-951', 'Night Sky Almanac', 'Central Branch', 'on-hold', 'AS-08', 'current');
