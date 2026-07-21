PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    incurred_on TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES expenses(stable_id),
    message TEXT NOT NULL
);

INSERT INTO expenses
    (stable_id, name, location, status, amount_cents, incurred_on, lifecycle)
VALUES
    ('exp-194', 'Chicago client rail fare', 'Chicago', 'approved', 4825, '2026-06-17', 'current'),
    ('exp-594', 'Boston volunteer lunch', 'Boston', 'submitted', 13640, '2026-06-19', 'current'),
    ('exp-994', 'Chicago client rail fare archive', 'Denver', 'closed', 4550, '2025-11-03', 'current'),
    ('exp-285', 'Chicago client rail fare', 'Boston', 'rejected', 5175, '2026-05-22', 'current'),
    ('exp-376', 'Chicago clients rail fare', 'Chicago', 'submitted', 4930, '2026-06-18', 'current'),
    ('exp-467', 'Chicago client rail fare', 'Chicago', 'void', 3900, '2025-09-14', 'stale'),
    ('exp-648', 'Boston volunteer lunches', 'Boston', 'approved', 14410, '2026-06-20', 'current'),
    ('exp-739', 'Boston volunteer lunch', 'Chicago', 'draft', 11980, '2026-06-10', 'current'),
    ('exp-820', 'Boston volunteer lunch', 'Boston', 'reimbursed', 12125, '2025-08-11', 'stale'),
    ('exp-911', 'Denver partner shuttle', 'Denver', 'approved', 6700, '2026-06-21', 'current');
