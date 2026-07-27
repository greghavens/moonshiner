PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    status TEXT NOT NULL,
    account_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    service_tier TEXT NOT NULL,
    details TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'draft', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES accounts(stable_id),
    message TEXT NOT NULL
);

CREATE INDEX account_scope_idx
ON accounts(name, region, lifecycle);

INSERT INTO accounts
    (stable_id, name, region, status, account_date, owner, service_tier, details, lifecycle)
VALUES
    ('acct-w4812', 'Arbor Foods renewal', 'West Region', 'renewal-review', '2026-09-14', 'Kira Moreno', 'enterprise', 'Annual terms awaiting customer review', 'current'),
    ('acct-c7734', 'Bright Dental onboarding', 'Central Region', 'implementation-scheduled', '2026-09-21', 'Jon Bell', 'growth', 'Implementation kickoff scheduled', 'current'),
    ('acct-w4812-draft', 'Arbor Foods renewal', 'West Region', 'draft', '2026-10-02', 'Kira Moreno', 'enterprise', 'Superseded planning draft', 'draft'),
    ('acct-e1403', 'Arbor Foods renewal', 'East Region', 'active', '2026-08-30', 'Asha Patel', 'standard', 'Different regional account', 'current'),
    ('acct-w6320', 'Arbor Foods renewal follow-up', 'West Region', 'active', '2026-09-19', 'Luis Ortega', 'standard', 'Related follow-up account', 'current'),
    ('acct-c7734-old', 'Bright Dental onboarding', 'Central Region', 'closed', '2025-11-10', 'Jon Bell', 'growth', 'Archived onboarding cycle', 'archived'),
    ('acct-s9940', 'Bright Dental onboarding', 'South Region', 'paused', '2026-09-25', 'Mina Shah', 'standard', 'Different regional account', 'current');

INSERT INTO notifications (stable_id, message)
VALUES ('acct-e1403', 'Existing unrelated notification');
