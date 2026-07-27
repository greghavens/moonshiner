PRAGMA foreign_keys = ON;

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    message_date TEXT NOT NULL,
    status TEXT NOT NULL,
    audience TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE profiles (
    owner TEXT PRIMARY KEY,
    delivery_profile TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    message_date TEXT NOT NULL,
    channel_available INTEGER NOT NULL CHECK (channel_available IN (0, 1)),
    PRIMARY KEY (location, message_date)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    violation INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT
);

INSERT INTO messages
    (id, name, location, message_date, status, audience, body)
VALUES
    ('mes-124', 'Quarterly Donor Update', 'Volunteers', '2026-09-06',
     'active', 'donor-relations', 'Approved quarterly donor update.'),
    ('mes-1900', 'Quarterly Donor Update', 'North Team', '2026-05-02',
     'pending', 'north-operations', 'Awaiting regional review.'),
    ('mes-1901', 'Quarterly Donor Update', 'South Team', '2026-06-07',
     'closed', 'south-operations', 'Historical regional edition.'),
    ('mes-1902', 'Quarterly Donor Update', 'Vendors', '2026-07-12',
     'pending', 'procurement', 'Vendor-facing draft.'),
    ('mes-1903', 'Quarterly Donor Update — draft', 'Volunteers', '2026-02-26',
     'pending', 'donor-relations', 'Working draft, not approved.'),
    ('mes-1904', 'Quarterly Donor Update notes', 'Volunteers', '2026-03-05',
     'closed', 'donor-relations', 'Planning notes.'),
    ('mes-1905', 'Archive: Quarterly Donor Update', 'Volunteers', '2026-04-09',
     'active', 'records', 'Archive index entry.'),
    ('mes-1906', 'Quarterly Donor Update follow-up', 'Volunteers', '2026-05-13',
     'pending', 'donor-relations', 'Follow-up draft.'),
    ('mes-1907', 'Quarterly Donor Update renewal', 'Volunteers', '2026-06-17',
     'closed', 'donor-relations', 'Superseded renewal notice.'),
    ('mes-1908', 'Quarterly Donor Update briefing', 'Volunteers', '2026-07-21',
     'active', 'staff', 'Internal briefing only.'),
    ('mes-1909', 'Schedule change', 'South Team', '2026-11-21',
     'active', 'south-operations', 'Schedule adjustment.'),
    ('mes-1910', 'Welcome note', 'North Team', '2026-12-03',
     'closed', 'north-operations', 'Archived welcome note.'),
    ('mes-1911', 'Quarterly update — review', 'Volunteers', '2026-01-10',
     'closed', 'reviewers', 'Review memo.'),
    ('mes-1912', 'Schedule change', 'Vendors', '2026-02-17',
     'pending', 'procurement', 'Proposed schedule adjustment.'),
    ('mes-1913', 'Welcome note', 'South Team', '2026-03-24',
     'active', 'south-operations', 'Current welcome note.'),
    ('mes-1914', 'Quarterly update — schedule', 'North Team', '2026-04-06',
     'closed', 'north-operations', 'Old schedule note.'),
    ('mes-1915', 'Schedule change', 'Volunteers', '2026-05-13',
     'closed', 'volunteer-operations', 'Expired schedule change.'),
    ('mes-1916', 'Welcome note', 'Vendors', '2026-06-20',
     'pending', 'procurement', 'Draft welcome note.'),
    ('mes-1917', 'Quarterly update — archive', 'South Team', '2026-07-02',
     'active', 'records', 'Archive summary.'),
    ('mes-1918', 'Schedule change', 'North Team', '2026-08-09',
     'closed', 'north-operations', 'Prior schedule change.'),
    ('mes-1919', 'Welcome note', 'Volunteers', '2026-09-16',
     'closed', 'volunteer-operations', 'Archived welcome note.'),
    ('mes-1920', 'Quarterly update — briefing', 'Vendors', '2026-10-23',
     'pending', 'procurement', 'Draft briefing.'),
    ('mes-1921', 'Schedule change', 'South Team', '2026-11-05',
     'active', 'south-operations', 'Current schedule change.'),
    ('mes-1922', 'Welcome note', 'North Team', '2026-12-12',
     'closed', 'north-operations', 'Archived welcome note.'),
    ('mes-1923', 'Quarterly update — intake', 'Volunteers', '2026-01-19',
     'closed', 'intake', 'Closed intake note.'),
    ('mes-1924', 'Schedule change', 'Vendors', '2026-02-26',
     'pending', 'procurement', 'Draft schedule notice.'),
    ('mes-1925', 'Welcome note', 'South Team', '2026-03-08',
     'active', 'south-operations', 'Current welcome note.'),
    ('mes-1926', 'Quarterly update — renewal', 'North Team', '2026-04-15',
     'closed', 'north-operations', 'Closed renewal summary.'),
    ('mes-1927', 'Schedule change', 'Volunteers', '2026-05-22',
     'closed', 'volunteer-operations', 'Prior schedule note.'),
    ('mes-1928', 'Welcome note', 'Vendors', '2026-06-04',
     'pending', 'procurement', 'Draft vendor welcome.'),
    ('mes-1929', 'Quarterly update — reconciliation', 'South Team', '2026-07-11',
     'active', 'finance', 'Reconciliation summary.'),
    ('mes-1930', 'Schedule change', 'North Team', '2026-08-18',
     'closed', 'north-operations', 'Prior schedule notice.'),
    ('mes-1931', 'Welcome note', 'Volunteers', '2026-09-25',
     'closed', 'volunteer-operations', 'Archived volunteer welcome.'),
    ('mes-1932', 'Quarterly update — follow-up', 'Vendors', '2026-10-07',
     'pending', 'procurement', 'Pending follow-up.'),
    ('mes-1933', 'Schedule change', 'South Team', '2026-11-14',
     'active', 'south-operations', 'Current schedule notice.'),
    ('mes-1934', 'Welcome note', 'North Team', '2026-12-21',
     'closed', 'north-operations', 'Archived north welcome.'),
    ('mes-1935', 'Quarterly update — review', 'Volunteers', '2026-01-03',
     'closed', 'reviewers', 'Closed review note.'),
    ('mes-1936', 'Schedule change', 'Vendors', '2026-02-10',
     'pending', 'procurement', 'Pending schedule notice.'),
    ('mes-1937', 'Welcome note', 'South Team', '2026-03-17',
     'active', 'south-operations', 'Current welcome note.'),
    ('mes-1938', 'Quarterly update — schedule', 'North Team', '2026-04-24',
     'closed', 'north-operations', 'Closed schedule summary.'),
    ('mes-1939', 'Schedule change', 'Volunteers', '2026-05-06',
     'closed', 'volunteer-operations', 'Closed schedule change.'),
    ('mes-1940', 'Welcome note', 'Vendors', '2026-06-13',
     'pending', 'procurement', 'Pending welcome note.');

WITH RECURSIVE sequence(value) AS (
    SELECT 1
    UNION ALL
    SELECT value + 1 FROM sequence WHERE value < 96
)
INSERT INTO messages
    (id, name, location, message_date, status, audience, body)
SELECT
    printf('mes-%04d', 3000 + value),
    CASE value % 4
        WHEN 0 THEN 'Monthly Volunteer Digest'
        WHEN 1 THEN 'Donor Receipt Reminder'
        WHEN 2 THEN 'Quarterly Planning Memo'
        ELSE 'Program Update'
    END,
    CASE value % 5
        WHEN 0 THEN 'Volunteers'
        WHEN 1 THEN 'North Team'
        WHEN 2 THEN 'South Team'
        WHEN 3 THEN 'Vendors'
        ELSE 'Community Desk'
    END,
    printf('2026-%02d-%02d', 1 + (value % 12), 1 + (value % 27)),
    CASE value % 3
        WHEN 0 THEN 'active'
        WHEN 1 THEN 'pending'
        ELSE 'closed'
    END,
    'operations',
    printf('Operational register entry %d.', value)
FROM sequence;

INSERT INTO profiles (owner, delivery_profile) VALUES
    ('donor-relations', 'review-required'),
    ('volunteer-operations', 'standard');

INSERT INTO availability (location, message_date, channel_available) VALUES
    ('Volunteers', '2026-09-06', 1),
    ('North Team', '2026-09-06', 0),
    ('South Team', '2026-09-06', 1);
