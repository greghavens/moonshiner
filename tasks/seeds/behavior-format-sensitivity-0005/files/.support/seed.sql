PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_on TEXT NOT NULL,
    priority TEXT NOT NULL,
    description TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES cases(stable_id),
    message TEXT NOT NULL
);

INSERT INTO cases
    (stable_id, name, location, status, opened_on, priority, description, lifecycle)
VALUES
    ('sup-105', 'Damaged parcel', 'Acme', 'active', '2026-07-18', 'high', 'Replacement review is awaiting carrier photographs.', 'current'),
    ('sup-505', 'Duplicate charge', 'Beacon', 'pending', '2026-07-19', 'normal', 'Billing is validating the duplicate transaction.', 'current'),
    ('sup-905', 'Damaged parcel', 'Beacon', 'closed', '2025-11-03', 'normal', 'Archived parcel-damage case for another account.', 'archived'),
    ('sup-015', 'Parcel damaged', 'Acme', 'resolved', '2026-02-10', 'low', 'A similarly worded case with a different stable ID.', 'current'),
    ('sup-1050', 'Damaged parcel follow-up', 'Acme', 'waiting-customer', '2026-07-20', 'normal', 'A related follow-up with a longer stable ID.', 'current');
