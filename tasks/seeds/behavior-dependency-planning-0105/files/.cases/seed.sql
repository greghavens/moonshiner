PRAGMA foreign_keys = ON;

CREATE TABLE case_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    customer TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_on TEXT NOT NULL,
    priority TEXT NOT NULL,
    description TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES case_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO case_records
    (stable_id, name, customer, status, opened_on, priority, description, lifecycle)
VALUES
    ('cas-205', 'Damaged parcel case', 'Acme Cooperative', 'investigating', '2026-07-14', 'high', 'Carrier damage review is in progress.', 'current'),
    ('cas-605', 'Duplicate membership charge', 'Beacon Arts', 'awaiting-refund', '2026-07-16', 'normal', 'Billing is validating the duplicate charge.', 'current'),
    ('cas-1005', 'Damaged parcel case archive', 'Acme Cooperative', 'resolved', '2025-07-14', 'normal', 'Archived case with a related name.', 'archived'),
    ('cas-316', 'Damaged parcels case', 'Acme Cooperative', 'open', '2026-07-18', 'normal', 'Pluralized parcel case.', 'current'),
    ('cas-427', 'Damaged parcel case', 'Acme Foundation', 'open', '2026-07-17', 'high', 'Case for a similarly named customer.', 'current'),
    ('cas-538', 'Damaged parcel case', 'Acme Cooperative', 'resolved', '2025-07-14', 'normal', 'Archived exact-name parcel case.', 'archived'),
    ('cas-649', 'Duplicate membership charges', 'Beacon Arts', 'investigating', '2026-07-19', 'normal', 'Pluralized membership charge case.', 'current'),
    ('cas-750', 'Duplicate membership charge', 'Beacon Artists', 'open', '2026-07-20', 'normal', 'Case for a similarly named customer.', 'current'),
    ('cas-861', 'Duplicate membership charge review', 'Beacon Arts', 'open', '2026-07-20', 'low', 'Related membership billing review.', 'current'),
    ('cas-972', 'Duplicate membership charge', 'Beacon Arts', 'resolved', '2025-07-16', 'normal', 'Archived exact-name membership case.', 'archived'),
    ('cas-083', 'Missing membership credit', 'Beacon Arts', 'waiting-customer', '2026-07-15', 'low', 'Separate membership credit question.', 'current');
