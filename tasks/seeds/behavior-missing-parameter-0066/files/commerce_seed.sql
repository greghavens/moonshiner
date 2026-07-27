PRAGMA foreign_keys = ON;

CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE profile_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    order_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO orders (id, name, location, status) VALUES
    ('com-166', 'Boise Office Furniture Order', 'Boise Office', 'active'),
    ('com-566', 'Boise Office Lighting Order', 'Boise Office', 'pending'),
    ('com-766', 'Phoenix Event Supply Order', 'Phoenix Studio', 'active');

INSERT INTO profile_preferences (preference_key, preference_value) VALUES
    ('preferred_fulfillment_location', 'Boise Office');
