PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = ON;

CREATE TABLE titles (
    position INTEGER NOT NULL UNIQUE,
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    date TEXT NOT NULL
);

CREATE TABLE profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE availability (
    title_id TEXT NOT NULL REFERENCES titles(id),
    location TEXT NOT NULL,
    available_copies INTEGER NOT NULL CHECK (available_copies >= 0)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id TEXT NOT NULL REFERENCES titles(id),
    message TEXT NOT NULL
);

INSERT INTO titles (position, id, name, location, status, date) VALUES
    (1, 'tit-904', 'The Quiet Atlas', 'East Branch', 'active', '2024-10-08'),
    (2, 'tit-318', 'Copper Horizon', 'East Branch', 'inactive', '2022-03-19'),
    (3, 'tit-731', 'A Map of Winter', 'East Branch', 'active', '2023-01-27'),
    (4, 'tit-055', 'Amber Orchard', 'West Branch', 'active', '2025-05-14'),
    (5, 'tit-142', 'Glass Rivers', 'East Branch', 'active', '2021-07-02'),
    (6, 'tit-667', 'East Branch Field Guide', 'North Branch', 'active', '2020-11-11'),
    (7, 'tit-809', 'Paper Constellations', 'East Branch Annex', 'active', '2024-06-21'),
    (8, 'tit-441', 'Glass Rivers', 'North Branch', 'active', '2019-09-03');

INSERT INTO profile (key, value) VALUES
    ('default_branch', 'Central Branch'),
    ('saved_view', 'recently-added');

INSERT INTO availability (title_id, location, available_copies) VALUES
    ('tit-731', 'East Branch', 2),
    ('tit-142', 'East Branch', 0),
    ('tit-904', 'East Branch', 1);
