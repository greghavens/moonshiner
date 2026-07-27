PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled', 'completed')),
    close_reason TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

CREATE TABLE close_attempts (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL REFERENCES meetings(id),
    message TEXT NOT NULL
);

INSERT INTO meetings (id, title, starts_at, status, close_reason, revision)
VALUES
    ('cal-074', 'Quarterly Finance Review', '2026-08-03T15:00:00Z', 'completed', NULL, 5),
    ('cal-141', 'Planning Review', '2026-08-07T16:30:00Z', 'active', NULL, 3),
    ('cal-219', 'Planning Review', '2026-08-12T17:00:00Z', 'active', NULL, 2),
    ('cal-308', 'Facilities Walkthrough', '2026-08-14T14:00:00Z', 'cancelled', 'building unavailable', 4);
