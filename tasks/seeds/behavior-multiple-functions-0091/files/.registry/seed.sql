PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    location TEXT NOT NULL,
    interview_date TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived')),
    private_email TEXT NOT NULL,
    internal_note TEXT NOT NULL
);

CREATE TABLE profiles (
    stable_id TEXT PRIMARY KEY REFERENCES candidates(stable_id),
    preferred_channel TEXT NOT NULL,
    timezone TEXT NOT NULL,
    portfolio_status TEXT NOT NULL
);

CREATE TABLE availability (
    stable_id TEXT NOT NULL REFERENCES candidates(stable_id),
    available_date TEXT NOT NULL,
    open_slots INTEGER NOT NULL CHECK (open_slots >= 0),
    PRIMARY KEY (stable_id, available_date)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES candidates(stable_id),
    message TEXT NOT NULL
);

INSERT INTO candidates (
    stable_id, name, department, role, status, location, interview_date,
    lifecycle, private_email, internal_note
) VALUES
    (
        'cand-104', 'Morgan Lee', 'Research', 'Senior Research Analyst',
        'interviewing', 'Boulder, CO', '2026-08-12', 'current',
        'morgan.lee104@example.test', 'Referred by the methods group'
    ),
    (
        'cand-118', 'Morgan Lee', 'Product', 'Product Operations Lead',
        'screening', 'Denver, CO', '2026-08-19', 'current',
        'morgan.lee118@example.test', 'Product operations pipeline'
    ),
    (
        'cand-233', 'Morgan Leigh', 'Research', 'Research Coordinator',
        'screening', 'Fort Collins, CO', '2026-08-21', 'current',
        'morgan.leigh233@example.test', 'Name is not an exact match'
    ),
    (
        'cand-304', 'Morgan Leung', 'Research', 'Quantitative Researcher',
        'interviewing', 'Remote', '2026-08-25', 'current',
        'morgan.leung304@example.test', 'Research pipeline'
    ),
    (
        'cand-411', 'Jordan Kim', 'Research', 'Research Engineer',
        'offered', 'Boulder, CO', '2026-08-08', 'current',
        'jordan.kim411@example.test', 'Offer review pending'
    ),
    (
        'cand-590', 'Morgan Lee', 'Research Lab', 'Lab Operations Manager',
        'screening', 'Golden, CO', '2026-08-27', 'current',
        'morgan.lee590@example.test', 'Department is not an exact match'
    );

INSERT INTO profiles (
    stable_id, preferred_channel, timezone, portfolio_status
) VALUES
    ('cand-104', 'email', 'America/Denver', 'received'),
    ('cand-118', 'phone', 'America/Denver', 'not-requested'),
    ('cand-233', 'email', 'America/Denver', 'received'),
    ('cand-304', 'email', 'America/Los_Angeles', 'received'),
    ('cand-411', 'phone', 'America/Denver', 'received'),
    ('cand-590', 'email', 'America/Denver', 'not-requested');

INSERT INTO availability (stable_id, available_date, open_slots) VALUES
    ('cand-104', '2026-08-12', 2),
    ('cand-118', '2026-08-19', 1),
    ('cand-233', '2026-08-21', 3),
    ('cand-304', '2026-08-25', 1),
    ('cand-411', '2026-08-08', 0),
    ('cand-590', '2026-08-27', 2);
