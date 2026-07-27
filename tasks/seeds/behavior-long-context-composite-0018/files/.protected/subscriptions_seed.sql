PRAGMA foreign_keys = ON;

CREATE TABLE subscriptions (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    account_reference TEXT NOT NULL,
    service_tier TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT NOT NULL,
    service_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL,
    PRIMARY KEY (location, service_date)
);

CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    preference TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT NOT NULL,
    message TEXT NOT NULL
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
    error TEXT,
    violation INTEGER NOT NULL DEFAULT 0
);

INSERT INTO subscriptions
    (stable_id, name, location, status, record_date, account_reference, service_tier, notes)
VALUES
    (
        'tel-118',
        'Fiber Plus Household Plan',
        'Family',
        'active',
        '2026-03-11',
        'acct-family-4408',
        'fiber-plus',
        'Primary household fiber service; verified during the March account review.'
    ),
    (
        'tel-1600',
        'Fiber Plus Household Plan',
        'Studio',
        'pending',
        '2026-11-20',
        'acct-studio-2881',
        'fiber-plus',
        'Studio installation awaiting a site survey.'
    ),
    (
        'tel-1601',
        'Fiber Plus Household Plan',
        'Personal',
        'closed',
        '2025-12-25',
        'acct-personal-1337',
        'fiber-plus',
        'Closed personal account retained for audit history.'
    ),
    (
        'tel-1602',
        'Fiber Plus Household Plan',
        'Field Team',
        'pending',
        '2026-01-06',
        'acct-field-7302',
        'fiber-plus',
        'Field deployment request still pending.'
    ),
    (
        'tel-1603',
        'Fiber Plus Household Plan — draft',
        'Family',
        'pending',
        '2026-08-20',
        'acct-family-4408',
        'draft',
        'Draft service-change proposal, not an active subscription.'
    ),
    (
        'tel-1604',
        'Fiber Plus Household Plan notes',
        'Family',
        'closed',
        '2026-09-24',
        'acct-family-4408',
        'internal',
        'Imported notes record with a similar label.'
    ),
    (
        'tel-1605',
        'Archive: Fiber Plus Household Plan',
        'Family',
        'active',
        '2025-10-03',
        'acct-family-legacy',
        'archive',
        'Historical snapshot; status reflects the state at archive time.'
    ),
    (
        'tel-1606',
        'Fiber Plus Household Plan follow-up',
        'Family',
        'pending',
        '2026-11-07',
        'acct-family-4408',
        'case',
        'Follow-up task, not a service subscription.'
    ),
    (
        'tel-1607',
        'Fiber Plus Household Plan renewal',
        'Family',
        'closed',
        '2026-12-11',
        'acct-family-4408',
        'case',
        'Completed renewal workflow record.'
    ),
    (
        'tel-1608',
        'Fiber Plus Household Plan briefing',
        'Family',
        'active',
        '2026-01-15',
        'acct-family-4408',
        'internal',
        'Briefing document registration entry.'
    ),
    (
        'tel-1609',
        'Hotspot plan',
        'Family',
        'active',
        '2026-05-15',
        'acct-family-4408',
        'mobile',
        'Shared family hotspot service.'
    ),
    (
        'tel-1610',
        'Mobile plan',
        'Field Team',
        'closed',
        '2026-06-22',
        'acct-field-0021',
        'mobile',
        'Decommissioned field mobile service.'
    ),
    (
        'tel-1611',
        'Tablet plan — reconciliation',
        'Personal',
        'closed',
        '2026-07-04',
        'acct-personal-7180',
        'case',
        'Reconciliation item for a retired tablet plan.'
    ),
    (
        'tel-1612',
        'Hotspot plan',
        'Studio',
        'pending',
        '2026-08-11',
        'acct-studio-2205',
        'mobile',
        'Studio hotspot order pending activation.'
    ),
    (
        'tel-1613',
        'Mobile plan',
        'Family',
        'active',
        '2026-09-18',
        'acct-family-2210',
        'mobile',
        'Family mobile line group.'
    ),
    (
        'tel-1614',
        'Tablet plan — follow-up',
        'Field Team',
        'closed',
        '2026-10-25',
        'acct-field-0294',
        'case',
        'Resolved device follow-up.'
    ),
    (
        'tel-1615',
        'Hotspot plan',
        'Personal',
        'closed',
        '2025-11-07',
        'acct-personal-9001',
        'mobile',
        'Expired hotspot subscription.'
    ),
    (
        'tel-1616',
        'Mobile plan',
        'Studio',
        'pending',
        '2026-12-14',
        'acct-studio-0088',
        'mobile',
        'Future studio line request.'
    ),
    (
        'tel-1617',
        'Tablet plan — review',
        'Family',
        'active',
        '2026-01-21',
        'acct-family-5051',
        'case',
        'Account review task for tablet services.'
    ),
    (
        'tel-1618',
        'Hotspot plan',
        'Field Team',
        'closed',
        '2026-02-03',
        'acct-field-1008',
        'mobile',
        'Former temporary event hotspot.'
    ),
    (
        'tel-1619',
        'Mobile plan',
        'Personal',
        'closed',
        '2026-03-10',
        'acct-personal-6104',
        'mobile',
        'Closed individual mobile line.'
    ),
    (
        'tel-1620',
        'Tablet plan — schedule',
        'Studio',
        'pending',
        '2026-04-17',
        'acct-studio-0447',
        'case',
        'Device rollout scheduling record.'
    ),
    (
        'tel-1621',
        'Hotspot plan',
        'Family',
        'active',
        '2026-05-24',
        'acct-family-6720',
        'mobile',
        'Secondary family hotspot.'
    ),
    (
        'tel-1622',
        'Mobile plan',
        'Field Team',
        'closed',
        '2026-06-06',
        'acct-field-8020',
        'mobile',
        'Closed seasonal field line.'
    ),
    (
        'tel-1623',
        'Tablet plan — archive',
        'Personal',
        'closed',
        '2025-07-13',
        'acct-personal-1102',
        'archive',
        'Archived tablet plan snapshot.'
    ),
    (
        'tel-1624',
        'Hotspot plan',
        'Studio',
        'pending',
        '2026-08-20',
        'acct-studio-3319',
        'mobile',
        'Pending production-floor hotspot.'
    ),
    (
        'tel-1625',
        'Mobile plan',
        'Family',
        'active',
        '2026-09-02',
        'acct-family-7900',
        'mobile',
        'Additional family mobile line.'
    ),
    (
        'tel-1626',
        'Tablet plan — briefing',
        'Field Team',
        'closed',
        '2026-10-09',
        'acct-field-7138',
        'internal',
        'Deployment briefing entry.'
    ),
    (
        'tel-1627',
        'Hotspot plan',
        'Personal',
        'closed',
        '2025-11-16',
        'acct-personal-7214',
        'mobile',
        'Canceled individual hotspot.'
    ),
    (
        'tel-1628',
        'Mobile plan',
        'Studio',
        'pending',
        '2026-12-23',
        'acct-studio-1886',
        'mobile',
        'Pending studio mobile bundle.'
    ),
    (
        'tel-1629',
        'Tablet plan — intake',
        'Family',
        'active',
        '2026-01-05',
        'acct-family-0092',
        'case',
        'Tablet intake task.'
    ),
    (
        'tel-1630',
        'Hotspot plan',
        'Field Team',
        'closed',
        '2026-02-12',
        'acct-field-4451',
        'mobile',
        'Completed project hotspot.'
    ),
    (
        'tel-1631',
        'Mobile plan',
        'Personal',
        'closed',
        '2026-03-19',
        'acct-personal-1390',
        'mobile',
        'Former personal line.'
    ),
    (
        'tel-1632',
        'Tablet plan — renewal',
        'Studio',
        'pending',
        '2026-04-26',
        'acct-studio-5600',
        'case',
        'Renewal task awaiting approval.'
    ),
    (
        'tel-1633',
        'Hotspot plan',
        'Family',
        'active',
        '2026-05-08',
        'acct-family-8411',
        'mobile',
        'Family travel hotspot.'
    ),
    (
        'tel-1634',
        'Mobile plan',
        'Field Team',
        'closed',
        '2026-06-15',
        'acct-field-6362',
        'mobile',
        'Retired field line.'
    ),
    (
        'tel-1635',
        'Tablet plan — reconciliation',
        'Personal',
        'closed',
        '2026-07-22',
        'acct-personal-8210',
        'case',
        'Closed reconciliation work item.'
    ),
    (
        'tel-1636',
        'Hotspot plan',
        'Studio',
        'pending',
        '2026-08-04',
        'acct-studio-9301',
        'mobile',
        'Pending studio hotspot request.'
    ),
    (
        'tel-1637',
        'Mobile plan',
        'Family',
        'active',
        '2026-09-11',
        'acct-family-3170',
        'mobile',
        'Family mobile add-on.'
    ),
    (
        'tel-1638',
        'Tablet plan — follow-up',
        'Field Team',
        'closed',
        '2026-10-18',
        'acct-field-6040',
        'case',
        'Resolved field tablet follow-up.'
    ),
    (
        'tel-1639',
        'Hotspot plan',
        'Personal',
        'closed',
        '2025-11-25',
        'acct-personal-2761',
        'mobile',
        'Closed personal hotspot.'
    ),
    (
        'tel-1640',
        'Mobile plan',
        'Studio',
        'pending',
        '2026-12-07',
        'acct-studio-2099',
        'mobile',
        'Studio mobile bundle pending provisioning.'
    );

INSERT INTO availability (location, service_date, open_slots) VALUES
    ('Family', '2026-03-11', 0),
    ('Studio', '2026-11-20', 3),
    ('Field Team', '2026-01-06', 1);

INSERT INTO profiles (profile_id, display_name, preference) VALUES
    ('profile-family', 'Family account defaults', 'paperless-billing'),
    ('profile-studio', 'Studio account defaults', 'monthly-summary');

PRAGMA user_version = 1;
