PRAGMA foreign_keys = ON;

CREATE TABLE campaigns (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    campaign_date TEXT NOT NULL,
    audience TEXT NOT NULL,
    subject TEXT NOT NULL,
    owner TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived')),
    last_updated TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES campaigns(stable_id),
    recipient TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO campaigns (
    stable_id, name, location, status, campaign_date, audience, subject,
    owner, lifecycle, last_updated
) VALUES
    ('mes-324', 'Museum donor thank-you', 'Donors', 'approved', '2026-09-09',
     'FY26 museum donors', 'Thank you for supporting the museum',
     'Development communications', 'current', '2026-07-18T15:40:00Z'),
    ('mes-724', 'Fall enrollment notice', 'Students', 'draft', '2026-09-11',
     'Continuing students', 'Fall enrollment opens soon',
     'Student communications', 'current', '2026-07-21T09:15:00Z'),
    ('mes-324-alt', 'Museum donor thank-you test', 'Staff', 'archived', '2026-09-09',
     'Internal reviewers', 'Test: thank you for supporting the museum',
     'Development communications', 'archived', '2026-07-10T12:00:00Z'),
    ('mes-324-draft', 'Museum donor thank-you draft', 'Donors', 'draft', '2026-09-10',
     'FY26 museum donors', 'Draft donor acknowledgement',
     'Development communications', 'current', '2026-07-20T11:30:00Z'),
    ('mes-724-copy', 'Fall enrollment notice draft copy', 'Students', 'draft', '2026-09-12',
     'Continuing students', 'Draft copy: fall enrollment',
     'Student communications', 'current', '2026-07-21T10:05:00Z'),
    ('mes-724-old', 'Fall enrollment notice', 'Alumni', 'archived', '2025-09-12',
     'Recent alumni', 'Prior-year enrollment notice',
     'Alumni communications', 'archived', '2025-10-01T08:00:00Z');
