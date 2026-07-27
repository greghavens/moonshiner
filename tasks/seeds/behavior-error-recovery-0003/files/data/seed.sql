PRAGMA page_size = 4096;
PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = ON;

CREATE TABLE bookings (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'waitlisted', 'cancelled')),
    stay_date TEXT NOT NULL,
    cancellation_reason TEXT,
    cancellation_count INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_count >= 0)
);

INSERT INTO bookings
    (stable_id, name, location, status, stay_date, cancellation_reason, cancellation_count)
VALUES
    ('hos-273', 'Riverside Interpreter Lodging', 'Portland', 'confirmed', '2026-11-08', NULL, 0),
    ('hos-643', 'Riverside Interpreter Lodging — Conference Hotel', 'Portland', 'confirmed', '2026-11-08', NULL, 0),
    ('hos-873', 'Riverside Interpreter Lodging', 'Vancouver', 'waitlisted', '2026-11-09', NULL, 0),
    ('hos-114', 'Northbank Captioner Lodging', 'Portland', 'confirmed', '2026-11-08', NULL, 0);

CREATE TABLE cancellation_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES bookings(stable_id),
    reason TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO saved_preferences VALUES
    ('default_city', 'Portland'),
    ('rooming_list_view', 'compact');

CREATE TABLE availability (
    location TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    rooms_available INTEGER NOT NULL CHECK (rooms_available >= 0),
    PRIMARY KEY (location, stay_date)
);

INSERT INTO availability VALUES
    ('Portland', '2026-11-08', 2),
    ('Vancouver', '2026-11-09', 1);

VACUUM;
