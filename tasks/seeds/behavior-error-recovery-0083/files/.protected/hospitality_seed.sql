PRAGMA foreign_keys = ON;

CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    reserved_for TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancellation-pending', 'cancelled')
    ),
    cancellation_reason TEXT,
    cancel_count INTEGER NOT NULL DEFAULT 0 CHECK (cancel_count >= 0)
);

INSERT INTO reservations (
    id, name, location, reserved_for, status, cancellation_reason, cancel_count
) VALUES
    ('hos-183', 'Juniper Hall reception', 'Boise', '2026-09-19T18:00:00-06:00', 'active', NULL, 0),
    ('hos-583', 'Juniper Hall reception quote', 'Meridian Events Office', '2026-09-19T18:00:00-06:00', 'active', NULL, 0),
    ('hos-247', 'Cedar Room breakfast service', 'Cedar Room', '2026-09-21T08:00:00-06:00', 'active', NULL, 0);
