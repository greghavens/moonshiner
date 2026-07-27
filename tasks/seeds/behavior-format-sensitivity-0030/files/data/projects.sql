PRAGMA foreign_keys = ON;

CREATE TABLE records (
    stable_id TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    title TEXT NOT NULL,
    project TEXT NOT NULL,
    status TEXT NOT NULL,
    assignee TEXT NOT NULL,
    details TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO records
    (stable_id, record_type, title, project, status, assignee, details)
VALUES
    ('pro-130', 'task_card', 'Policy review 030', 'Beacon',
     'active', 'Mina Okafor',
     'Review the retention policy changes before the governance meeting.'),
    ('pro-530', 'project_note', 'Newsletter draft 030', 'Clover',
     'pending', 'Eli Navarro',
     'Editorial notes for the next community newsletter draft.'),
    ('pro-131', 'task_card', 'Vendor access audit', 'Beacon',
     'blocked', 'Tara Singh',
     'Confirm vendor accounts before the quarterly access review.'),
    ('pro-129', 'task_card', 'Policy archive cleanup', 'Beacon Archive',
     'complete', 'Archive Desk',
     'Prior-year policy card retained for historical reference.');
