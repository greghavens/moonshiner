PRAGMA foreign_keys = ON;

CREATE TABLE support_cases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    workspace TEXT NOT NULL,
    status TEXT NOT NULL,
    requester TEXT NOT NULL,
    summary TEXT NOT NULL
);

INSERT INTO support_cases
    (id, title, workspace, status, requester, summary)
VALUES
    ('sup-275', 'Duplicate Invoice Attachment Case', 'Northwind Support',
     'open', 'Reese Morgan',
     'Duplicate intake confirmed by requester before overnight handoff.'),
    ('sup-611', 'Invoice Attachment Missing Case', 'Northwind Support',
     'open', 'Reese Morgan',
     'Canonical case retained for continued investigation.'),
    ('sup-844', 'Duplicate Invoice Attachment Case', 'Cedar Arts Sandbox',
     'open', 'Taylor Sato',
     'Separate sandbox case with a similar title.'),
    ('sup-119', 'Payment Export Column Case', 'Northwind Support',
     'pending', 'Jamie Ortiz',
     'Unrelated support intake awaiting triage.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('support-lead', 'queue-view=overnight-handoff');

CREATE TABLE availability (
    queue_name TEXT NOT NULL,
    queue_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (queue_name, queue_date)
);

INSERT INTO availability (queue_name, queue_date, open_slots)
VALUES
    ('overnight', '2026-07-22', 8),
    ('priority', '2026-07-22', 3);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    committed INTEGER NOT NULL DEFAULT 0,
    result_digest TEXT,
    error_kind TEXT
);
