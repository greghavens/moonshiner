PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE facilities_requests (
    request_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    request_date TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    details TEXT NOT NULL
);

CREATE TABLE mutation_log (
    entry_id INTEGER PRIMARY KEY,
    operation TEXT NOT NULL,
    request_id TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE notification_log (
    entry_id INTEGER PRIMARY KEY,
    request_id TEXT,
    recipient TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

INSERT INTO metadata (key, value) VALUES
    ('audit_key', 'facilitydesk-0017-receipt-key-47dc31b6d621');

INSERT INTO facilities_requests (
    request_id, name, location, request_date, status,
    priority, requested_by, details
) VALUES
    (
        'fac-117',
        'Atrium Lighting Repair',
        'Building A',
        '2026-08-02',
        'vendor-assigned',
        'high',
        'Site Operations',
        'Replace failed drivers and test the east atrium lighting circuit.'
    ),
    (
        'fac-517',
        'Training Room Setup',
        'Building B',
        '2026-08-03',
        'scheduled',
        'normal',
        'Learning Services',
        'Configure seating, display, and audio for the onboarding session.'
    ),
    (
        'fac-917',
        'Atrium Lighting Repair',
        'Building A Annex',
        '2025-08-02',
        'closed',
        'low',
        'Annex Reception',
        'Historical annex request retained as a location distractor.'
    ),
    (
        'fac-817',
        'Training Room Setup',
        'Building B Annex',
        '2025-08-03',
        'closed',
        'low',
        'Annex Programs',
        'Historical annex request retained as a location distractor.'
    );

PRAGMA user_version = 1;
