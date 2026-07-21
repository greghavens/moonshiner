PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    coordinator TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (location, meeting_date)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE audit_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    error TEXT,
    violation INTEGER NOT NULL DEFAULT 0
);

INSERT INTO meetings (id, name, location, meeting_date, status, owner, notes) VALUES
    (
        'mtg_7ea42c19',
        'Vendor Accessibility Check-In',
        'Remote - Meet',
        '2026-07-24',
        'confirmed',
        'Vendor Operations',
        'Review accessibility testing follow-ups with the current vendor team.'
    ),
    (
        'mtg_b18d907e',
        'Facilities Safety Debrief',
        'Boston Office',
        '2026-07-18',
        'completed',
        'Regional Facilities',
        'Close out the quarterly evacuation and incident-response exercise.'
    ),
    (
        'mtg_hist_104a',
        'Vendor Accessibility Check-In',
        'Remote - Meet Archive',
        '2025-07-24',
        'archived',
        'Vendor Operations',
        'Historical location retained for audit purposes.'
    ),
    (
        'mtg_hist_30c7',
        'Vendor Accessibility Check-In - 2025',
        'Remote - Meet',
        '2025-03-08',
        'completed',
        'Vendor Operations',
        'Historical title retained for audit purposes.'
    ),
    (
        'mtg_hist_55d1',
        'Facilities Safety Debrief',
        'Boston Office Archive',
        '2025-07-18',
        'archived',
        'Regional Facilities',
        'Historical location retained for audit purposes.'
    ),
    (
        'mtg_hist_819f',
        'Facilities Safety Debrief - 2025',
        'Boston Office',
        '2025-02-11',
        'completed',
        'Regional Facilities',
        'Historical title retained for audit purposes.'
    );

INSERT INTO saved_preferences (coordinator, value) VALUES
    ('operations', 'regional-brief-defaults');

INSERT INTO availability (location, meeting_date, open_slots) VALUES
    ('Remote - Meet', '2026-07-24', 2),
    ('Boston Office', '2026-07-18', 0);

PRAGMA user_version = 1;
