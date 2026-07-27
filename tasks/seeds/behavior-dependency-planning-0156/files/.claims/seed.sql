PRAGMA foreign_keys = ON;

CREATE TABLE claim_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    status TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES claim_records(stable_id)
);

INSERT INTO claim_records
    (stable_id, name, region, status, lifecycle)
VALUES
    ('ins-256', 'Kitchen smoke claim', 'Mountain', 'under-review', 'current'),
    ('ins-656', 'Storm debris claim', 'Gulf', 'documents-needed', 'current'),
    ('ins-1056', 'Kitchen smoke claim archive', 'Pacific', 'closed', 'archived');
