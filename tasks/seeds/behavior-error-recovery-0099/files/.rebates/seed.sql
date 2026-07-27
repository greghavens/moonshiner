PRAGMA foreign_keys = ON;

CREATE TABLE applications (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    district TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_reason TEXT
);

CREATE TABLE cancellation_requests (
    request_number INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status_before TEXT NOT NULL,
    status_after TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES applications(stable_id)
);

INSERT INTO applications (stable_id, name, district, status, cancel_reason) VALUES
    ('pub-199', 'Garden water rebate — Elm Street', 'Central', 'active', NULL),
    ('pub-919', 'Garden water rebate — Elm Street Extension', 'Central', 'active', NULL),
    ('pub-299', 'Rain barrel rebate — Elm Street', 'Central', 'cancellation-pending', NULL);
