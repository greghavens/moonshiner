PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancellation-pending', 'cancelled')
    ),
    cancellation_reason TEXT,
    cancel_count INTEGER NOT NULL DEFAULT 0 CHECK (cancel_count >= 0)
);

INSERT INTO meetings (
    id, name, location, scheduled_at, status, cancellation_reason, cancel_count
) VALUES
    ('cal-181', 'Museum Partnership Briefing', 'West Gallery Conference Room', '2026-09-17T14:30:00-06:00', 'active', NULL, 0),
    ('cal-581', 'Museum Partnership Briefing rehearsal', 'Online', '2026-09-16T14:30:00-06:00', 'active', NULL, 0),
    ('cal-246', 'Collections transport review', 'Archive Reading Room', '2026-09-21T09:00:00-06:00', 'active', NULL, 0);
