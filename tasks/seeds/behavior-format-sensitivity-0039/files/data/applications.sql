PRAGMA foreign_keys = ON;

CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO applications
    (
        id,
        name,
        location,
        status
    )
VALUES
    (
        'pub-139',
        'Pet license 039',
        'Aurora',
        'active'
    ),
    (
        'pub-539',
        'Facility permit 039',
        'Lakewood',
        'pending'
    ),
    (
        'pub-319',
        'Street-use permit 019',
        'Boulder',
        'on_hold'
    ),
    (
        'pub-139-legacy',
        'Pet license 039',
        'Aurora',
        'expired'
    );
