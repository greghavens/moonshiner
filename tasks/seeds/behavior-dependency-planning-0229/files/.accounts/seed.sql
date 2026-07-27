PRAGMA foreign_keys = ON;

CREATE TABLE account_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    account_owner TEXT NOT NULL,
    commitment_cents INTEGER NOT NULL,
    contact TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES account_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO account_records
    (stable_id, name, region, status, record_date, account_owner,
     commitment_cents, contact, notes, lifecycle)
VALUES
    ('acct-ne-229', 'Cobalt Museum sponsorship', 'Northeast', 'active', '2026-08-12', 'Mara Bell', 2750000, 'partnerships@cobaltmuseum.org', 'Annual public-program sponsorship account.', 'current'),
    ('acct-sw-729', 'Delta Housing expansion', 'Southwest', 'pending-review', '2026-09-03', 'Theo Ruiz', 4600000, 'projects@deltahousing.org', 'Regional affordable-housing expansion account.', 'current'),
    ('acct-ne-330', 'Cobalt Museum sponsorship renewal', 'Northeast', 'draft', '2026-10-15', 'Mara Bell', 2900000, 'partnerships@cobaltmuseum.org', 'Related renewal planning account.', 'current'),
    ('acct-mw-431', 'Cobalt Museum sponsorship', 'Midwest', 'active', '2026-08-19', 'Ivan Shaw', 1800000, 'midwest@cobaltmuseum.org', 'Same account name in another region.', 'current'),
    ('acct-ne-532', 'Cobalt Museum sponsorship', 'Northeast', 'closed', '2025-08-12', 'Mara Bell', 2400000, 'partnerships@cobaltmuseum.org', 'Archived prior-year sponsorship account.', 'archived'),
    ('acct-sw-633', 'Delta Housing expansions', 'Southwest', 'approved', '2026-08-30', 'Theo Ruiz', 4150000, 'projects@deltahousing.org', 'Pluralized program account.', 'current'),
    ('acct-se-834', 'Delta Housing expansion', 'Southeast', 'on-hold', '2026-09-07', 'Rina Cole', 3900000, 'southeast@deltahousing.org', 'Same account name in another region.', 'current'),
    ('acct-sw-935', 'Delta Housing expansion review', 'Southwest', 'scheduled', '2026-08-28', 'Theo Ruiz', 0, 'projects@deltahousing.org', 'Related review-only account.', 'current'),
    ('acct-sw-036', 'Delta Housing expansion', 'Southwest', 'closed', '2025-09-03', 'Theo Ruiz', 3720000, 'projects@deltahousing.org', 'Archived earlier expansion account.', 'archived'),
    ('acct-ne-137', 'Harbor Library endowment', 'Northeast', 'active', '2026-07-24', 'Lena Park', 1350000, 'giving@harborlibrary.org', 'Separate cultural account.', 'current');
