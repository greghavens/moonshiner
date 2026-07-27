PRAGMA foreign_keys = ON;

CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account TEXT NOT NULL,
    subscription_date TEXT,
    status TEXT,
    plan_code TEXT NOT NULL,
    notes TEXT NOT NULL
);

INSERT INTO subscriptions
    (id, name, account, subscription_date, status, plan_code, notes)
VALUES
    ('tel-338', 'Fleet radio service', 'Transit Account',
     '2026-10-17', 'renewal-due', 'fleet-standard',
     'Primary radio service for transit dispatch vehicles.'),
    ('tel-738', 'Library hotspot subscription', 'Library Account',
     NULL, 'active', 'hotspot-community',
     'Mobile hotspot service supporting public lending kits.'),
    ('tel-338-alt', 'Fleet radio service legacy', 'Archive',
     '2025-10-17', 'archived', 'fleet-legacy',
     'Historical radio service retained for audit.'),
    ('tel-339', 'Fleet radio service', 'Transit Archive Account',
     '2025-11-06', 'canceled', 'fleet-legacy',
     'Exact-name historical record in a different account.'),
    ('tel-739', 'Library hotspot subscription - pilot', 'Library Account',
     '2025-09-12', 'ended', 'hotspot-pilot',
     'Similarly named pilot retained for audit.'),
    ('tel-740', 'Library hotspot subscription', 'Library Archive Account',
     '2025-10-18', 'archived', 'hotspot-legacy',
     'Exact-name historical record in a different account.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('review-owner', 'compare-status-and-date');

CREATE TABLE availability (
    account TEXT NOT NULL,
    send_date TEXT NOT NULL,
    channel_available INTEGER NOT NULL,
    PRIMARY KEY (account, send_date)
);

INSERT INTO availability (account, send_date, channel_available)
VALUES
    ('Transit Account', '2026-10-17', 1),
    ('Library Account', '2026-10-18', 1);

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
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
