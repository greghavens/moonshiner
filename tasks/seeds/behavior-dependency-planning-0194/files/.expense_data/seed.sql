PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    stable_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    submitted_by TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES expenses(stable_id),
    message TEXT NOT NULL
);

INSERT INTO expenses
    (stable_id, description, city, status, expense_date, amount_cents,
     submitted_by, cost_center, lifecycle)
VALUES
    ('exp-4187', 'Chicago rail fare — outreach trip', 'Chicago', 'approved',
     '2026-07-18', 1845, 'Mina Patel', 'Community Outreach', 'current'),
    ('exp-7724', 'Boston team lunch — budget workshop', 'Boston', 'pending-review',
     '2026-07-19', 12680, 'Noah Williams', 'Finance Enablement', 'current'),
    ('exp-1261', 'Chicago rail fare — outreach trip', 'Boston', 'submitted',
     '2026-07-17', 2120, 'Avery Chen', 'Field Programs', 'current'),
    ('exp-2395', 'Chicago rail fare - outreach trip', 'Chicago', 'rejected',
     '2026-07-16', 1795, 'Robin Garcia', 'Community Outreach', 'current'),
    ('exp-3058', 'Chicago rail fare — outreach trip', 'Chicago', 'reimbursed',
     '2025-07-18', 1675, 'Mina Patel', 'Community Outreach', 'archived'),
    ('exp-5462', 'Boston team lunch — budget workshop', 'Chicago', 'approved',
     '2026-07-20', 11840, 'Taylor Brown', 'Finance Enablement', 'current'),
    ('exp-6840', 'Boston team lunch — budget workshop', 'Boston', 'reimbursed',
     '2025-07-19', 10950, 'Noah Williams', 'Finance Enablement', 'archived'),
    ('exp-8933', 'Boston team lunches — budget workshop', 'Boston', 'submitted',
     '2026-07-21', 13110, 'Jordan Lee', 'Finance Enablement', 'current');
