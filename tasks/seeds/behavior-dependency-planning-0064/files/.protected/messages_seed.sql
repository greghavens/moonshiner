PRAGMA foreign_keys = ON;

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    send_date TEXT NOT NULL,
    status TEXT NOT NULL,
    channel TEXT NOT NULL,
    owner TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

INSERT INTO messages
    (id, name, location, send_date, status, channel, owner, internal_note)
VALUES
    ('msg_7ad91c', 'Donor Reception Follow-Up', 'Advancement',
     '2026-07-24', 'ready for review', 'email', 'Annual Giving',
     'Audience exclusions are maintained by Advancement Operations.'),
    ('msg_c48e26', 'Exhibit Opening Invitation', 'Museum Members',
     '2026-07-25', 'scheduled', 'email', 'Member Programs',
     'Final image rights were cleared in the collection system.'),
    ('msg_10b5fd', 'Donor Reception Follow-Up - 2025', 'Advancement',
     '2025-07-18', 'sent', 'email', 'Annual Giving',
     'Historical campaign retained for audit.'),
    ('msg_3f280a', 'Donor Reception Follow-Up', 'Alumni Relations',
     '2026-07-26', 'draft', 'email', 'Alumni Engagement',
     'Separate audience and approval chain.'),
    ('msg_65a17b', 'Exhibit Opening Invitation (Press)', 'Museum Members',
     '2026-07-23', 'sent', 'email', 'Communications',
     'Press variant retained for reporting.'),
    ('msg_f296d4', 'Exhibit Opening Invitation', 'Public Programs',
     '2026-07-25', 'paused', 'email', 'Public Programs',
     'Public-program audience is managed separately.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('communications-lead', 'weekly-review');

CREATE TABLE availability (
    location TEXT NOT NULL,
    send_date TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    PRIMARY KEY (location, send_date)
);

INSERT INTO availability (location, send_date, capacity)
VALUES
    ('Advancement', '2026-07-24', 3),
    ('Museum Members', '2026-07-25', 2);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    parent_exe TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    signature TEXT
);
