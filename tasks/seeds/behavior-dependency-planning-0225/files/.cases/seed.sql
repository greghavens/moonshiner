PRAGMA foreign_keys = ON;

CREATE TABLE case_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    queue TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
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
    (stable_id, name, queue, status, record_date, priority, description, lifecycle)
VALUES
    ('cas-225', 'Learning portal access failure', 'Education Queue', 'investigating', '2026-07-18', 'high', 'Access diagnostics are in progress.', 'current'),
    ('cas-725', 'Incorrect roaming fee', 'Telecom Queue', 'awaiting-credit', '2026-07-20', 'normal', 'Billing is reviewing the roaming adjustment.', 'current'),
    ('cas-326', 'Learning portal access failures', 'Education Queue', 'open', '2026-07-19', 'normal', 'Pluralized access case.', 'current'),
    ('cas-427', 'Learning portal access failure', 'Student Services Queue', 'open', '2026-07-17', 'high', 'A similarly named case in another queue.', 'current'),
    ('cas-528', 'Learning portal access failure', 'Education Queue', 'resolved', '2025-07-18', 'normal', 'Archived exact-name access case.', 'archived'),
    ('cas-629', 'Incorrect roaming fees', 'Telecom Queue', 'investigating', '2026-07-21', 'normal', 'Pluralized roaming fee case.', 'current'),
    ('cas-830', 'Incorrect roaming fee', 'Mobile Support Queue', 'open', '2026-07-21', 'normal', 'A similarly named case in another queue.', 'current'),
    ('cas-931', 'Incorrect roaming fee review', 'Telecom Queue', 'open', '2026-07-21', 'low', 'Related roaming billing review.', 'current'),
    ('cas-032', 'Incorrect roaming fee', 'Telecom Queue', 'resolved', '2025-07-20', 'normal', 'Archived exact-name roaming case.', 'archived'),
    ('cas-133', 'Course enrollment mismatch', 'Education Queue', 'waiting-customer', '2026-07-16', 'low', 'Separate education support question.', 'current');
