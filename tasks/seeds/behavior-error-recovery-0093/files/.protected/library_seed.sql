PRAGMA foreign_keys = ON;

CREATE TABLE library_titles (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancellation-pending', 'cancelled')
    ),
    cancellation_reason TEXT
);

CREATE TABLE settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    seal TEXT NOT NULL
);

INSERT INTO library_titles(
    id, title, collection_name, scheduled_date, status, cancellation_reason
) VALUES
    (
        'lib-193',
        'Oral Histories of Mesa County',
        'Western Colorado Archive',
        '2026-10-08',
        'active',
        NULL
    ),
    (
        'lib-593',
        'Oral Histories of Mesa County: Field Notes',
        'Acquisitions Review',
        '2026-10-08',
        'active',
        NULL
    ),
    (
        'lib-241',
        'Grand Valley Irrigation Ledgers',
        'Western Colorado Archive',
        '2026-10-14',
        'cancelled',
        'duplicate digitization request'
    );

INSERT INTO settings(name, value)
VALUES ('first_target_cancel_timeout_consumed', '0');
