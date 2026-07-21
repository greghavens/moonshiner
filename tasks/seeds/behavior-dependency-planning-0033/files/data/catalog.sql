PRAGMA foreign_keys = ON;

CREATE TABLE records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL
);

INSERT INTO records(stable_id, name, location, status, record_date) VALUES
    ('lib-133', 'Orchard Weather Journal', 'North Branch', 'checked-out', '2026-09-25'),
    ('lib-533', 'A Field Guide to Civic Murals', 'Downtown Branch', 'available', '2026-09-01'),
    ('lib-933', 'Orchard Weather Journal', 'North Branch Archive', 'withdrawn', '2025-09-25'),
    ('lib-833', 'A Field Guide to Civic Murals', 'Downtown Branch Storage', 'withdrawn', '2025-09-01');

INSERT INTO saved_preferences(preference_key, preference_value) VALUES
    ('circulation_view', 'compact'),
    ('date_display', 'iso-8601');
