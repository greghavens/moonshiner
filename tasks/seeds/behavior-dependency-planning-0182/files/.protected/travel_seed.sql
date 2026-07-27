PRAGMA foreign_keys = ON;

CREATE TABLE trips (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    trip_date TEXT,
    status TEXT NOT NULL,
    planner TEXT NOT NULL,
    notes TEXT NOT NULL,
    cancellation_reason TEXT
);

INSERT INTO trips
    (id, name, location, trip_date, status, planner, notes, cancellation_reason)
VALUES
    ('tra-282', 'Reykjavík research trip', 'Reykjavík', '2026-10-06',
     'approved', 'Marta Jónsdóttir',
     'Current research itinerary for the northern archives.', NULL),
    ('tra-682', 'Chicago volunteer summit', 'Chicago', NULL,
     'draft', 'Jordan Lee',
     'Current volunteer summit itinerary; travel date is not assigned.', NULL),
    ('tra-1082', 'Reykjavík research trip archive', 'Oslo', '2025-09-11',
     'closed', 'Nora Berg',
     'Archived similarly named record in another location.', NULL),
    ('tra-482', 'Reykjavík research trip', 'Akureyri', '2025-10-03',
     'closed', 'Marta Jónsdóttir',
     'Historical trip with the same name but a different location.', NULL),
    ('tra-1682', 'Chicago volunteer summit archive', 'Chicago', '2025-05-14',
     'cancelled', 'Jordan Lee',
     'Archived similarly named Chicago itinerary.', 'Event completed'),
    ('tra-882', 'Chicago volunteer summit', 'Evanston', '2025-05-12',
     'closed', 'Avery Patel',
     'Historical same-name record outside Chicago.', NULL),
    ('tra-901', 'Great Lakes partner forum', 'Chicago', '2026-11-18',
     'approved', 'Sam Rivera',
     'Unrelated current Chicago itinerary.', NULL);

CREATE TABLE saved_profiles (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_profiles (owner, preference)
VALUES ('travel-desk', 'show-current-itineraries');

CREATE TABLE availability (
    location TEXT NOT NULL,
    trip_date TEXT NOT NULL,
    available_options INTEGER NOT NULL,
    PRIMARY KEY (location, trip_date)
);

INSERT INTO availability (location, trip_date, available_options)
VALUES
    ('Reykjavík', '2026-10-06', 3),
    ('Chicago', '2026-11-18', 5);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    outcome TEXT NOT NULL,
    delivered INTEGER NOT NULL CHECK (delivered IN (0, 1))
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    status TEXT,
    outcome TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
