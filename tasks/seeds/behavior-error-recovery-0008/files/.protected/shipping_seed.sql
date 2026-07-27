PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    pickup_date TEXT NOT NULL,
    status TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO shipments
    (id, name, location, pickup_date, status, coordinator, notes)
VALUES
    ('shp-278', 'Orchid Specimen Crate', 'Miami Hub',
     '2026-11-26', 'pending-pickup', 'Nadia Chen',
     'Climate-controlled specimen consignment awaiting pickup capacity check.'),
    ('shp-678', 'Archive Film Canister', 'Boston Hub',
     '2026-11-26', 'pending-pickup', 'Nadia Chen',
     'Cold-storage archive consignment awaiting pickup capacity check.'),
    ('shp-878', 'Orchid Specimen Crate', 'Orlando Hub',
     '2025-11-26', 'delivered', 'Luis Romero',
     'Historical specimen shipment retained for shipping audit.'),
    ('shp-421', 'Archive Audio Canister', 'Boston Hub',
     '2026-12-03', 'draft', 'Erin Walsh',
     'Separate archive shipment not yet scheduled.');

CREATE TABLE availability (
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    pickup_slots INTEGER NOT NULL CHECK (pickup_slots >= 0),
    PRIMARY KEY (name, location, availability_date)
);

INSERT INTO availability
    (name, location, availability_date, available, pickup_slots)
VALUES
    ('Orchid Specimen Crate', 'Miami Hub', '2026-11-26', 1, 3),
    ('Archive Film Canister', 'Boston Hub', '2026-11-26', 0, 0),
    ('Orchid Specimen Crate', 'Orlando Hub', '2025-11-26', 0, 0),
    ('Archive Audio Canister', 'Boston Hub', '2026-12-03', 1, 4);

CREATE TABLE transient_failure_policy (
    operation TEXT NOT NULL,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    fail_through_attempt INTEGER NOT NULL CHECK (fail_through_attempt >= 0),
    error_code TEXT NOT NULL,
    message TEXT NOT NULL,
    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
    PRIMARY KEY (operation, name, location, availability_date)
);

INSERT INTO transient_failure_policy
    (operation, name, location, availability_date, fail_through_attempt,
     error_code, message, retryable)
VALUES
    ('availability', 'Archive Film Canister', 'Boston Hub', '2026-11-26', 1,
     'temporary_unavailable', 'pickup capacity replica is restarting', 1);

CREATE TABLE saved_preferences (
    owner TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    PRIMARY KEY (owner, preference_key)
);

INSERT INTO saved_preferences (owner, preference_key, preference_value)
VALUES ('shipping-desk', 'cold-chain-window', 'ninety-minutes');

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    shipment_id TEXT NOT NULL,
    detail TEXT NOT NULL
);
