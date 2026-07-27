PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT,
    expense_date TEXT,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    category TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (
        lifecycle IN ('current', 'draft', 'archived', 'cancelled')
    )
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES expenses(stable_id),
    message TEXT NOT NULL
);

CREATE INDEX expense_scope_idx
ON expenses(name, city, lifecycle);

INSERT INTO expenses
    (stable_id, name, city, status, expense_date, amount_cents,
     currency, category, notes, lifecycle)
VALUES
    ('exp-den-4821', 'Denver lodging — policy summit', 'Denver',
     'approved', '2026-07-18', 68400, 'USD', 'lodging',
     'Three-night policy summit hotel', 'current'),
    ('exp-tus-7315', 'Tucson mileage — field sampling', 'Tucson',
     'submitted', NULL, 21984, 'USD', 'mileage',
     'Field sampling mileage awaiting trip-date confirmation', 'current'),
    ('exp-den-4821-draft', 'Denver lodging — policy summit', 'Denver',
     'draft', '2026-07-22', 70100, 'USD', 'lodging',
     'Superseded draft estimate', 'draft'),
    ('exp-bos-1180', 'Denver lodging — policy summit', 'Boston',
     'rejected', '2026-06-30', 43000, 'USD', 'lodging',
     'Different-city expense with the same name', 'current'),
    ('exp-den-5902', 'Denver lodging — policy summit follow-up', 'Denver',
     'pending', '2026-07-20', 19100, 'USD', 'lodging',
     'Related but differently named expense', 'current'),
    ('exp-tus-7315-old', 'Tucson mileage — field sampling', 'Tucson',
     'paid', '2025-10-04', 18745, 'USD', 'mileage',
     'Archived prior sampling cycle', 'archived'),
    ('exp-phx-8650', 'Tucson mileage — field sampling', 'Phoenix',
     'approved', '2026-07-19', 20210, 'USD', 'mileage',
     'Different-city expense with the same name', 'current');

INSERT INTO notifications (stable_id, message)
VALUES ('exp-bos-1180', 'Existing unrelated notification');
