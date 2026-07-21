PRAGMA foreign_keys = ON;

CREATE TABLE claims (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    office TEXT NOT NULL,
    status TEXT NOT NULL,
    policy_number TEXT NOT NULL,
    opened_on TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES claims(stable_id),
    message TEXT NOT NULL
);

INSERT INTO claims
    (stable_id, name, office, status, policy_number, opened_on, lifecycle)
VALUES
    ('clm-196', 'Bicycle theft claim', 'West Office', 'under-review', 'POL-W-1842', '2026-06-12', 'current'),
    ('clm-596', 'Windshield damage claim', 'North Office', 'approved', 'POL-N-7315', '2026-06-18', 'current'),
    ('clm-107', 'Bicycle theft claim archive', 'West Office', 'closed', 'POL-W-0988', '2025-09-03', 'current'),
    ('clm-248', 'Bicycle theft claim', 'East Office', 'awaiting-documents', 'POL-E-3521', '2026-05-29', 'current'),
    ('clm-329', 'Bicycle theft claims', 'West Office', 'approved', 'POL-W-4706', '2026-06-13', 'current'),
    ('clm-410', 'Bicycle theft recovery claim', 'West Office', 'investigating', 'POL-W-5220', '2026-06-14', 'current'),
    ('clm-481', 'Bicycle theft claim', 'West Office', 'closed', 'POL-W-0114', '2025-08-21', 'archived'),
    ('clm-627', 'Windshield damage claims', 'North Office', 'under-review', 'POL-N-8043', '2026-06-19', 'current'),
    ('clm-708', 'Windshield damage claim', 'South Office', 'denied', 'POL-S-2661', '2026-06-02', 'current'),
    ('clm-789', 'Windshield damage assessment', 'North Office', 'scheduled', 'POL-N-6197', '2026-06-17', 'current'),
    ('clm-860', 'Windshield damage claim', 'North Office', 'closed', 'POL-N-0275', '2025-07-16', 'archived'),
    ('clm-941', 'Basement water claim', 'Central Office', 'submitted', 'POL-C-4409', '2026-06-20', 'current');
