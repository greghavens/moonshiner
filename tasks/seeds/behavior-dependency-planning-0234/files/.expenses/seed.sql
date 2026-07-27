PRAGMA foreign_keys = ON;

CREATE TABLE expense_records (
    expense_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id TEXT NOT NULL REFERENCES expense_records(expense_id),
    message TEXT NOT NULL
);

INSERT INTO expense_records
    (expense_id, description, city, status, expense_date, amount_cents,
     currency, cost_center, notes, lifecycle)
VALUES
    ('expense-pdx-234', 'Portland supplies — volunteer fair', 'Portland', 'approved', '2026-07-20', 18475, 'USD', 'community-events', 'Reusable signs and table materials for the volunteer fair.', 'current'),
    ('expense-rdu-734', 'Raleigh taxi — museum loan', 'Raleigh', 'pending-review', '2026-07-21', 6890, 'USD', 'collections-logistics', 'Ground transport associated with the outgoing museum loan.', 'current'),
    ('expense-sea-134', 'Portland supplies — volunteer fair', 'Seattle', 'submitted', '2026-07-19', 9050, 'USD', 'community-events', 'Same description filed from another city.', 'current'),
    ('expense-pdx-334', 'Portland supplies - volunteer fair', 'Portland', 'approved', '2026-07-18', 13200, 'USD', 'community-events', 'ASCII-hyphen description is a distinct expense.', 'current'),
    ('expense-pdx-434', 'Portland supply — volunteer fair', 'Portland', 'rejected', '2026-07-17', 5100, 'USD', 'community-events', 'Singular description is a distinct expense.', 'current'),
    ('expense-pdx-534', 'Portland supplies — volunteer fair', 'Portland', 'reimbursed', '2025-05-10', 17125, 'USD', 'community-events-archive', 'Archived prior event expense.', 'archived'),
    ('expense-dur-634', 'Raleigh taxi — museum loan', 'Durham', 'approved', '2026-07-21', 7425, 'USD', 'collections-logistics', 'Same description filed from another city.', 'current'),
    ('expense-rdu-834', 'Raleigh taxi - museum loan', 'Raleigh', 'approved', '2026-07-20', 6500, 'USD', 'collections-logistics', 'ASCII-hyphen description is a distinct expense.', 'current'),
    ('expense-rdu-934', 'Raleigh taxi — museum loan', 'Raleigh', 'reimbursed', '2025-03-14', 6120, 'USD', 'collections-logistics-archive', 'Archived prior loan expense.', 'archived'),
    ('expense-den-034', 'Denver lodging — oral history visit', 'Denver', 'paid', '2026-07-16', 28600, 'USD', 'field-research', 'Unrelated current expense.', 'current');
