PRAGMA foreign_keys = ON;

CREATE TABLE subscriptions (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account TEXT NOT NULL,
    status TEXT NOT NULL,
    service_type TEXT NOT NULL,
    renewal_date TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES subscriptions(stable_id),
    message TEXT NOT NULL
);

INSERT INTO subscriptions
    (stable_id, name, account, status, service_type, renewal_date, lifecycle)
VALUES
    ('tel-198', 'Family fiber subscription', 'Family Account', 'active', 'fiber', '2027-02-01', 'current'),
    ('tel-598', 'Studio tablet plan', 'Studio Account', 'pending-activation', 'tablet-data', '2027-03-15', 'current'),
    ('tel-109', 'Family fiber subscription archive', 'Family Account', 'closed', 'fiber', '2025-02-01', 'current'),
    ('tel-219', 'Family fiber subscription', 'Guest Account', 'suspended', 'fiber', '2026-12-01', 'current'),
    ('tel-330', 'Family fibre subscription', 'Family Account', 'active', 'fiber', '2027-01-10', 'current'),
    ('tel-441', 'Family fiber subscription', 'Family Account', 'closed', 'fiber', '2025-08-11', 'archived'),
    ('tel-628', 'Studio tablet plans', 'Studio Account', 'active', 'tablet-data', '2027-04-02', 'current'),
    ('tel-739', 'Studio tablet plan', 'Workshop Account', 'active', 'tablet-data', '2027-05-19', 'current'),
    ('tel-840', 'Studio tablet plan assessment', 'Studio Account', 'requested', 'consultation', '2026-10-22', 'current'),
    ('tel-951', 'Studio tablet plan', 'Studio Account', 'closed', 'tablet-data', '2025-10-14', 'archived'),
    ('tel-062', 'Field phone plan', 'Field Account', 'active', 'mobile-data', '2027-06-30', 'current');
