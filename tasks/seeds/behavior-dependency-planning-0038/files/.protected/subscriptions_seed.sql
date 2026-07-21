PRAGMA foreign_keys = ON;

CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    started_on TEXT NOT NULL,
    status TEXT NOT NULL,
    plan TEXT NOT NULL,
    account_owner TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO subscriptions
    (id, name, location, started_on, status, plan, account_owner, notes)
VALUES
    ('sub-481', 'Museum Guest Wi-Fi Subscription', 'Gallery Network',
     '2026-02-15', 'active', 'managed-wireless', 'Mara Ives',
     'Current guest-network subscription for the gallery account review.'),
    ('sub-736', 'Clinic Backup Line Subscription', 'Cedar Clinic',
     '2026-03-08', 'paused', 'continuity-line', 'Dev Shah',
     'Current backup-line subscription pending clinic authorization.'),
    ('sub-104', 'Museum Guest Wi-Fi Subscription', 'Gallery Network Annex',
     '2024-11-03', 'archived', 'managed-wireless', 'Mara Ives',
     'Historical subscription for the annex network.'),
    ('sub-295', 'Museum Guest Wi-Fi Subscription - Legacy', 'Gallery Network',
     '2023-06-12', 'canceled', 'legacy-wireless', 'Nolan Beck',
     'Retired similarly named gallery subscription.'),
    ('sub-352', 'Clinic Backup Line Subscription', 'Cedar Clinic East',
     '2024-09-19', 'archived', 'continuity-line', 'Dev Shah',
     'Historical backup line for another clinic location.'),
    ('sub-918', 'Clinic Backup Line Subscription (Historical)', 'Cedar Clinic',
     '2023-05-27', 'canceled', 'legacy-line', 'Lena Wu',
     'Retired similarly named clinic subscription.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('account-review', 'show-current-subscriptions');

CREATE TABLE availability (
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    technician_slots INTEGER NOT NULL,
    PRIMARY KEY (location, service_date)
);

INSERT INTO availability (location, service_date, technician_slots)
VALUES
    ('Gallery Network', '2026-07-24', 2),
    ('Cedar Clinic', '2026-07-24', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    action_id TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
