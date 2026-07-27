PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    id TEXT PRIMARY KEY,
    merchant TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    currency TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO expenses
    (id, merchant, expense_date, amount_cents, currency, category, status)
VALUES
    ('exp-134', 'Northstar Office Supply', '2026-02-11', 4876, 'USD', 'office_supplies', 'approved'),
    ('exp-534', 'Harbor Street Cafe', '2026-02-12', 1935, 'USD', 'meals', 'submitted'),
    ('exp-314', 'Civic Center Parking', '2026-02-10', 2400, 'USD', 'transportation', 'approved'),
    ('exp-134-archive', 'Northstar Office Supply', '2025-02-11', 4621, 'USD', 'office_supplies', 'archived');
