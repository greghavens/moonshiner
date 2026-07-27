PRAGMA foreign_keys = ON;

CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    guest_ref TEXT NOT NULL,
    arrival_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled', 'completed')),
    cancellation_reason TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

CREATE TABLE cancellation_attempts (
    reservation_id TEXT PRIMARY KEY REFERENCES reservations(id),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL REFERENCES reservations(id),
    message TEXT NOT NULL
);

INSERT INTO reservations
    (id, guest_ref, arrival_date, status, cancellation_reason, revision)
VALUES
    ('hos-052', 'guest-801', '2026-09-02', 'completed', NULL, 4),
    ('hos-143', 'guest-417', '2026-09-18', 'active', NULL, 2),
    ('hos-219', 'guest-417', '2026-09-19', 'active', NULL, 1),
    ('hos-330', 'guest-665', '2026-10-04', 'cancelled', 'duplicate booking', 3);
