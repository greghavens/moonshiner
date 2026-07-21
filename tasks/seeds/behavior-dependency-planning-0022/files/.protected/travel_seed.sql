PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    trip_date TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    desk TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    trip_date TEXT NOT NULL,
    open_seats INTEGER NOT NULL,
    PRIMARY KEY (location, trip_date)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL,
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

INSERT INTO trips (id, name, location, trip_date, status, owner, notes) VALUES
    (
        'tra_6c91f27d',
        'Oslo Design Workshop',
        'Oslo',
        '2026-08-17',
        'ticketed',
        'Design Programs',
        'Current workshop itinerary pending final desk reconciliation.'
    ),
    (
        'tra_b04e8a31',
        'Lisbon Partner Summit',
        'Lisbon',
        '2026-08-18',
        'lodging-pending',
        'Partner Operations',
        'Current summit itinerary awaiting lodging confirmation.'
    ),
    (
        'tra_hist_9f02',
        'Oslo Design Workshop',
        'Oslo Airport Transit',
        '2025-08-17',
        'cancelled',
        'Design Programs',
        'Historical transit entry retained for audit purposes.'
    ),
    (
        'tra_hist_73aa',
        'Oslo Design Workshop - 2025',
        'Oslo',
        '2025-03-14',
        'completed',
        'Design Programs',
        'Historical workshop entry retained for audit purposes.'
    ),
    (
        'tra_hist_d120',
        'Lisbon Partner Summit',
        'Lisbon Transit Desk',
        '2025-08-18',
        'cancelled',
        'Partner Operations',
        'Historical transit entry retained for audit purposes.'
    ),
    (
        'tra_hist_50e4',
        'Lisbon Partner Summit - 2025',
        'Lisbon',
        '2025-04-09',
        'completed',
        'Partner Operations',
        'Historical summit entry retained for audit purposes.'
    );

INSERT INTO saved_preferences (desk, value) VALUES
    ('travel', 'approval-queue-defaults');

INSERT INTO availability (location, trip_date, open_seats) VALUES
    ('Oslo', '2026-08-17', 3),
    ('Lisbon', '2026-08-18', 1);

PRAGMA user_version = 1;
