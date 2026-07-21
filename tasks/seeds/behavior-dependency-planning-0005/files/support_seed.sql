PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    case_date TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner TEXT NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE availability (
    team TEXT NOT NULL,
    shift_date TEXT NOT NULL,
    agents_available INTEGER NOT NULL,
    PRIMARY KEY (team, shift_date)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    case_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO cases
    (id, name, location, case_date, status, priority, owner, summary)
VALUES
    ('sup-105', 'Damaged Cold-Chain Parcel', 'Alder Foods',
     '2026-08-16', 'open', 'urgent', 'Mira Chen',
     'Outer carton crushed; temperature logger review is in progress.'),
    ('sup-505', 'Duplicate Annual Charge', 'Beacon Arts',
     '2026-08-17', 'pending-customer', 'normal', 'Theo Grant',
     'Billing requested the second receipt before issuing a correction.'),
    ('sup-905', 'Damaged Cold-Chain Parcel', 'Alder Foods - Former',
     '2025-08-16', 'resolved', 'normal', 'Archive Queue',
     'Prior-site record retained for audit history.'),
    ('sup-805', 'Duplicate Annual Charge', 'Beacon Arts Sandbox',
     '2025-08-17', 'resolved', 'low', 'Training Queue',
     'Sandbox billing case used for agent training.'),
    ('sup-315', 'Damaged Ambient Parcel', 'Alder Foods',
     '2026-08-18', 'open', 'normal', 'Nora Bell',
     'Non-refrigerated delivery damage claim.'),
    ('sup-615', 'Annual Plan Renewal Question', 'Beacon Arts',
     '2026-08-19', 'waiting-internal', 'low', 'Sam Ortiz',
     'Customer asked for renewal-term clarification.');

INSERT INTO saved_preferences (preference_key, preference_value) VALUES
    ('default_queue', 'priority-support'),
    ('date_display', 'iso-8601');

INSERT INTO availability (team, shift_date, agents_available) VALUES
    ('cold-chain', '2026-08-17', 2),
    ('billing', '2026-08-17', 1);
