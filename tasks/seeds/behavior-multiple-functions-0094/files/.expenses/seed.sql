PRAGMA foreign_keys = ON;

CREATE TABLE expense_records (
    stable_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    location TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'cancelled')),
    employee TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

CREATE TABLE preference_fields (
    field_name TEXT PRIMARY KEY,
    display_order INTEGER NOT NULL UNIQUE
);

CREATE TABLE profile_preferences (
    profile_id TEXT NOT NULL,
    field_name TEXT NOT NULL REFERENCES preference_fields(field_name),
    field_value TEXT NOT NULL,
    PRIMARY KEY (profile_id, field_name)
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    available_date TEXT NOT NULL,
    reviewer_capacity INTEGER NOT NULL,
    PRIMARY KEY (location, available_date)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES expense_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO expense_records (
    stable_id, description, amount_cents, status, location, expense_date,
    lifecycle, employee, internal_note
) VALUES
    (
        'exp-094', 'Regional rail ticket', 6840, 'submitted', 'Denver',
        '2026-07-14', 'current', 'A. Rivera',
        'Record data is unrelated to operational preferences'
    ),
    (
        'exp-311', 'Client-site lodging', 24800, 'approved', 'Fort Collins',
        '2026-07-08', 'current', 'A. Rivera',
        'Do not notify without explicit authorization'
    ),
    (
        'exp-527', 'Airport shuttle', 3600, 'cancelled', 'Denver',
        '2026-06-29', 'cancelled', 'A. Rivera',
        'Historical cancelled record'
    );

INSERT INTO preference_fields (field_name, display_order) VALUES
    ('approval_route', 1),
    ('receipt_capture', 2),
    ('reimbursement_method', 3),
    ('submission_cadence', 4),
    ('mileage_unit', 5);

INSERT INTO profile_preferences (
    profile_id, field_name, field_value
) VALUES
    ('primary', 'approval_route', 'Manager then Finance'),
    ('primary', 'receipt_capture', 'Mobile scan'),
    ('primary', 'reimbursement_method', 'ACH to checking ending 1842'),
    ('primary', 'submission_cadence', 'Every Friday');

INSERT INTO availability (
    location, available_date, reviewer_capacity
) VALUES
    ('Denver', '2026-07-24', 3),
    ('Denver', '2026-07-25', 1),
    ('Fort Collins', '2026-07-24', 2);
