PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vehicle_type TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    service_date TEXT NOT NULL,
    odometer_miles INTEGER NOT NULL,
    assigned_route TEXT
);

INSERT INTO vehicles
    (
        id,
        name,
        vehicle_type,
        location,
        status,
        service_date,
        odometer_miles,
        assigned_route
    )
VALUES
    (
        'fle-140',
        'Shuttle 30 040',
        'passenger shuttle',
        'Depot D',
        'active',
        '2026-06-18',
        48320,
        'Route 30'
    ),
    (
        'fle-540',
        'Van 12 040',
        'cargo van',
        'Depot A',
        'pending',
        '2026-07-28',
        27104,
        NULL
    ),
    (
        'fle-104',
        'Shuttle 03 104',
        'passenger shuttle',
        'Depot C',
        'maintenance',
        '2026-07-22',
        61903,
        NULL
    ),
    (
        'fle-140-archive',
        'Shuttle 30 040',
        'passenger shuttle',
        'Depot B',
        'retired',
        '2023-03-09',
        112778,
        NULL
    );
