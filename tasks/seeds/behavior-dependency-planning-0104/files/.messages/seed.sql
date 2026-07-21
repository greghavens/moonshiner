PRAGMA foreign_keys = ON;

CREATE TABLE message_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    channel TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    body TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES message_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO message_records
    (stable_id, name, location, status, channel, scheduled_for, body, lifecycle)
VALUES
    ('mes-204', 'Volunteer renewal reminder', 'Volunteers', 'sent', 'email', '2026-06-10T09:00:00-06:00', 'Renewal reminder for active volunteers.', 'current'),
    ('mes-604', 'North team quarterly update', 'North Team', 'draft', 'newsletter', '2026-08-03T10:30:00-06:00', 'Quarterly update for the North Team.', 'current'),
    ('mes-1004', 'Volunteer renewal reminder archive', 'All Staff', 'closed', 'email', '2025-06-10T09:00:00-06:00', 'Archived organization-wide reminder.', 'archived'),
    ('mes-315', 'Volunteer renewal reminders', 'Volunteers', 'draft', 'email', '2026-07-24T09:00:00-06:00', 'Draft pluralized reminder.', 'current'),
    ('mes-426', 'Volunteer renewal reminder', 'North Team', 'scheduled', 'email', '2026-07-25T09:00:00-06:00', 'North Team volunteer reminder.', 'current'),
    ('mes-537', 'Volunteer renewal reminder', 'Volunteers', 'closed', 'email', '2025-06-10T09:00:00-06:00', 'Archived Volunteer reminder.', 'archived'),
    ('mes-648', 'North teams quarterly update', 'North Team', 'sent', 'newsletter', '2026-06-30T10:30:00-06:00', 'Pluralized North Team update.', 'current'),
    ('mes-759', 'North team quarterly update', 'Volunteers', 'scheduled', 'newsletter', '2026-08-04T10:30:00-06:00', 'Volunteer copy of the quarterly update.', 'current'),
    ('mes-860', 'North team quarterly update draft', 'North Team', 'draft', 'newsletter', '2026-08-05T10:30:00-06:00', 'Related North Team draft.', 'current'),
    ('mes-971', 'North team quarterly update', 'North Team', 'closed', 'newsletter', '2025-08-03T10:30:00-06:00', 'Archived North Team update.', 'archived'),
    ('mes-082', 'South team quarterly update', 'South Team', 'sent', 'newsletter', '2026-07-01T10:30:00-06:00', 'South Team quarterly update.', 'current');
