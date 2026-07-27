PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    location TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived')),
    owner TEXT NOT NULL,
    annual_value INTEGER NOT NULL,
    internal_note TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    available_date TEXT NOT NULL,
    appointment_count INTEGER NOT NULL,
    PRIMARY KEY (location, available_date)
);

CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY,
    default_location TEXT NOT NULL,
    notification_channel TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES accounts(stable_id),
    message TEXT NOT NULL
);

INSERT INTO accounts (
    stable_id, name, status, location, scheduled_date, lifecycle, owner,
    annual_value, internal_note
) VALUES
    (
        'crm-042', 'Alpine Signal Works', 'active', 'Boulder, CO',
        '2026-08-11', 'current', 'D. Alvarez', 76000,
        'Quarterly review pending'
    ),
    (
        'crm-188', 'Juniper Ridge Labs', 'onboarding', 'Aurora, CO',
        '2026-08-16', 'current', 'M. Okafor', 114000,
        'Do not send an update without approval'
    ),
    (
        'crm-189', 'Juniper Ridge Dental', 'scheduled', 'Denver, CO',
        '2026-08-17', 'current', 'R. Chen', 92000,
        'Stable ID confirmed by account operations'
    ),
    (
        'crm-190', 'Juniper Ridge Design', 'scheduled', 'Denver, CO',
        '2026-08-18', 'current', 'S. Patel', 68000,
        'Possible text-search collision'
    ),
    (
        'crm-721', 'Mesa Garden Supply', 'inactive', 'Pueblo, CO',
        '2026-09-03', 'archived', 'A. Ibrahim', 51000,
        'Historical record'
    );

INSERT INTO availability (
    location, available_date, appointment_count
) VALUES
    ('Denver, CO', '2026-08-17', 3),
    ('Denver, CO', '2026-08-18', 1),
    ('Aurora, CO', '2026-08-16', 2);

INSERT INTO profiles (
    profile_id, default_location, notification_channel
) VALUES
    ('primary', 'Denver, CO', 'email');
