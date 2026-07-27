PRAGMA foreign_keys = ON;

CREATE TABLE visits (
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    visit_date TEXT NOT NULL,
    availability TEXT NOT NULL,
    PRIMARY KEY (name, city, visit_date)
);

INSERT INTO visits (name, city, visit_date, availability) VALUES
    ('Kyoto visit', 'Kyoto', '2026-09-15', 'spaces available'),
    ('Montreal visit', 'Montreal', '2026-09-15', 'waitlist only'),
    ('Kyoto visit', 'Osaka', '2026-09-15', 'sold out'),
    ('Montreal visit', 'Montreal', '2026-09-16', 'spaces available'),
    ('Oslo visit', 'Oslo', '2026-09-15', 'limited availability');

CREATE TABLE transient_failures (
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    visit_date TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY (name, city, visit_date)
);

INSERT INTO transient_failures (name, city, visit_date, remaining)
VALUES ('Montreal visit', 'Montreal', '2026-09-15', 1);

CREATE TABLE operation_state (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    arguments_json TEXT NOT NULL,
    start_ns INTEGER NOT NULL,
    end_ns INTEGER,
    process_pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    process_group_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    outcome TEXT
);
