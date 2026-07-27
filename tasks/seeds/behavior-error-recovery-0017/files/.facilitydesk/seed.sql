PRAGMA foreign_keys = ON;

CREATE TABLE facilities_requests (
    request_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    requested_for TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    details TEXT NOT NULL,
    cancel_reason TEXT,
    cancellation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES facilities_requests(request_id),
    message TEXT NOT NULL
);

CREATE TABLE service_control (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO facilities_requests (
    request_id, name, location, requested_for, status, priority,
    requested_by, details, cancel_reason, cancellation_count
) VALUES
    (
        'fac-117', 'Lighting repair', 'Building A', '2026-08-02',
        'active', 'high', 'Site Operations',
        'Replace failed drivers and test the east atrium lighting circuit.',
        NULL, 0
    ),
    (
        'fac-517', 'Room setup', 'Building B', '2026-08-03',
        'pending', 'normal', 'Learning Services',
        'Configure seating, display, and audio for the onboarding session.',
        NULL, 0
    ),
    (
        'fac-617', 'Lighting repair', 'Building B', '2025-08-02',
        'closed', 'low', 'Building Services',
        'Historical lighting request retained as a same-name distractor.',
        NULL, 0
    );

INSERT INTO service_control (key, value)
VALUES ('lose_next_cancel_response', '1');
