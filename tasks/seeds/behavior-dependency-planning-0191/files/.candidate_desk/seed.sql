PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO candidates
    (stable_id, name, location, status, record_date, coordinator, notes, lifecycle)
VALUES
    ('rec-291', 'Casey Evans — Data Steward', 'Research', 'interview',
     '2026-08-13', 'Morgan Lee', 'Panel materials are ready.', 'current'),
    ('rec-691', 'Devon Flores — Outreach Coordinator', 'Community Programs',
     'cancellation-requested', '2026-08-15', 'Jordan Kim',
     'Candidate requested withdrawal before the scheduled screen.', 'current'),
    ('rec-291-alt', 'Casey Evans — Data Steward referral', 'Talent Pool',
     'archived', '2026-08-13', 'Archive Desk',
     'Historical referral retained for reporting.', 'archived'),
    ('rec-891', 'Devon Flores — Outreach Coordinator', 'Community Programs Archive',
     'archived', '2025-08-15', 'Archive Desk',
     'Historical outreach record.', 'archived');
