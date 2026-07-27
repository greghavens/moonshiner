PRAGMA foreign_keys = ON;

CREATE TABLE campaigns (
    stable_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    collection TEXT NOT NULL,
    status TEXT NOT NULL,
    campaign_date TEXT NOT NULL,
    subject TEXT NOT NULL,
    audience TEXT NOT NULL,
    channel TEXT NOT NULL,
    owner TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE INDEX campaigns_lookup
    ON campaigns(title, collection, lifecycle, stable_id);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES campaigns(stable_id)
);

INSERT INTO campaigns
    (stable_id, title, collection, status, campaign_date, subject,
     audience, channel, owner, lifecycle)
VALUES
    ('cmp-1093', 'Volunteer renewal reminder', 'Community Outreach', 'draft', '2026-08-09', 'Renew your volunteer registration', 'Community volunteers', 'email', 'Ari Moreno', 'current'),
    ('cmp-2047', 'Volunteer renewal reminder', 'Volunteers', 'scheduled', '2026-08-12', 'Please renew your volunteer registration', 'Active volunteers with expiring registrations', 'email', 'Ari Moreno', 'current'),
    ('cmp-2874', 'Volunteer renewal reminder', 'Volunteers', 'sent', '2025-08-11', 'Volunteer renewal reminder', 'Prior-year volunteers', 'email', 'Ari Moreno', 'archived'),
    ('cmp-3328', 'Volunteer renewal reminder draft', 'Volunteers', 'draft', '2026-08-15', 'Draft renewal message', 'Volunteer coordinators', 'email', 'Ari Moreno', 'current'),
    ('cmp-6140', 'North region service bulletin', 'Service Operations', 'approved', '2026-07-17', 'North region service changes', 'Service subscribers', 'email', 'Morgan Lee', 'current'),
    ('cmp-7812', 'North region service bulletin', 'North Region', 'sent', '2026-07-18', 'Scheduled service changes for the North region', 'North region subscribers', 'email', 'Morgan Lee', 'current'),
    ('cmp-8465', 'North region service bulletin', 'North Region', 'sent', '2025-07-19', 'Archived North region bulletin', 'Prior-year subscribers', 'email', 'Morgan Lee', 'archived'),
    ('cmp-9301', 'North region service bulletin — follow-up', 'North Region', 'draft', '2026-07-25', 'Possible follow-up service note', 'North region subscribers', 'email', 'Morgan Lee', 'current');

INSERT INTO notifications (stable_id, message)
VALUES
    ('cmp-2874', 'Prior-year delivery confirmation retained for audit'),
    ('cmp-8465', 'Archived campaign delivery confirmation retained for audit');
