PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    interview_date TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    interview_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (location, interview_date)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO candidates
    (id, name, location, status, interview_date, coordinator, notes)
VALUES
    ('rec-111', 'Casey Evans - Regional Sales', 'Sales',
     'panel-interview', '2026-08-09', 'Morgan Lee',
     'Regional panel packet is complete.'),
    ('rec-511', 'Devon Flores - Research Analyst', 'Research',
     'reference-check', '2026-08-10', 'Jordan Kim',
     'Two references have responded.'),
    ('rec-911', 'Casey Evans - Regional Sales', 'Sales Alumni',
     'withdrawn', '2025-08-09', 'Archive Desk',
     'Historical candidate record.'),
    ('rec-811', 'Devon Flores - Research Analyst', 'Research Archive',
     'withdrawn', '2025-08-10', 'Archive Desk',
     'Historical candidate record.');

INSERT INTO saved_preferences (preference_key, preference_value) VALUES
    ('default_view', 'active-candidates'),
    ('timezone', 'America/Denver');

INSERT INTO availability (location, interview_date, open_slots) VALUES
    ('Sales', '2026-08-09', 2),
    ('Research', '2026-08-10', 1);
