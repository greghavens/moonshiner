PRAGMA foreign_keys = ON;

CREATE TABLE subscriptions (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'cancelled')),
    UNIQUE (name, account, lifecycle)
);

CREATE TABLE availability (
    stable_id TEXT NOT NULL REFERENCES subscriptions(stable_id),
    service_date TEXT NOT NULL,
    availability TEXT NOT NULL CHECK (availability IN ('available', 'unavailable')),
    PRIMARY KEY (stable_id, service_date)
);

CREATE TABLE transient_rules (
    stable_id TEXT NOT NULL REFERENCES subscriptions(stable_id),
    service_date TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL CHECK (failed_attempts >= 0),
    PRIMARY KEY (stable_id, service_date)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES subscriptions(stable_id),
    message TEXT NOT NULL
);

INSERT INTO subscriptions (stable_id, name, account, lifecycle) VALUES
    ('sub-health-204', 'Clinic backup line', 'Health Account', 'current'),
    ('sub-arts-731', 'Museum guest Wi-Fi plan', 'Arts Account', 'current'),
    ('sub-health-118', 'Clinic backup line archive', 'Health Account', 'cancelled'),
    ('sub-arts-509', 'Museum guest Wi-Fi', 'Arts Account', 'current'),
    ('sub-civic-415', 'Museum guest Wi-Fi plan', 'Civic Account', 'current');

INSERT INTO availability (stable_id, service_date, availability) VALUES
    ('sub-health-204', '2026-09-06', 'available'),
    ('sub-health-204', '2026-09-13', 'unavailable'),
    ('sub-arts-731', '2026-09-06', 'unavailable'),
    ('sub-arts-731', '2026-09-13', 'available'),
    ('sub-arts-509', '2026-09-06', 'available'),
    ('sub-civic-415', '2026-09-06', 'available');

INSERT INTO transient_rules (stable_id, service_date, failed_attempts) VALUES
    ('sub-arts-731', '2026-09-06', 1);
