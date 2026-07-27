PRAGMA foreign_keys = ON;

CREATE TABLE calendar_entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    organizer TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT
);

INSERT INTO calendar_entries
    (
        id,
        title,
        location,
        starts_at,
        ends_at,
        organizer,
        status,
        notes
    )
VALUES
    (
        'cal-141',
        'Planning Review 041',
        'Denver',
        '2026-07-24T09:30:00-06:00',
        '2026-07-24T10:15:00-06:00',
        'Operations Planning',
        'active',
        'Quarterly capacity and milestone review'
    ),
    (
        'cal-541',
        'Budget Sync 041',
        'Chicago',
        '2026-07-23T14:00:00-05:00',
        '2026-07-23T14:45:00-05:00',
        'Finance Operations',
        'pending',
        'Previous entry retained for archive continuity'
    ),
    (
        'cal-114',
        'Planning Review 014',
        'Boulder',
        '2026-07-25T11:00:00-06:00',
        '2026-07-25T11:30:00-06:00',
        'Program Delivery',
        'active',
        NULL
    ),
    (
        'cal-141-archive',
        'Planning Review 041',
        'Denver',
        '2025-07-24T09:30:00-06:00',
        '2025-07-24T10:15:00-06:00',
        'Operations Planning',
        'archived',
        'Prior-year event with a similar identifier'
    );
