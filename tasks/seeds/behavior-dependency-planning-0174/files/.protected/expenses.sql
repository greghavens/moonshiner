PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'submitted', 'needs-receipt', 'approved', 'reimbursed')
    ),
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    cost_center TEXT NOT NULL
);

INSERT INTO expenses (
    id, title, city, status, amount, currency, expense_date, submitted_by, cost_center
) VALUES
    ('exp-318', 'North Loop mileage claim', 'Chicago', 'approved', '86.40', 'USD', '2026-06-14', 'Elena Brooks', 'Field Operations'),
    ('exp-742', 'After-school supplies receipt', 'Boston', 'submitted', '132.58', 'USD', '2026-06-18', 'Marcus Lee', 'Community Programs'),
    ('exp-943', 'North Loop mileage claim', 'Boston', 'reimbursed', '74.10', 'USD', '2026-05-27', 'Iris Grant', 'Regional Programs'),
    ('exp-1107', 'After-school supplies receipt', 'Chicago', 'approved', '98.25', 'USD', '2026-05-30', 'Naomi Ross', 'Youth Services'),
    ('exp-1288', 'After-school supply receipt', 'Boston', 'needs-receipt', '41.75', 'USD', '2026-06-02', 'Theo Ward', 'Community Programs');

CREATE TABLE mutation_receipts (
    receipt TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    created_ns INTEGER NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1))
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    mutation_receipt TEXT NOT NULL UNIQUE REFERENCES mutation_receipts(receipt),
    created_ns INTEGER NOT NULL
);

CREATE TABLE audit_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    process_id INTEGER NOT NULL,
    parent_process_id INTEGER NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    result_count INTEGER,
    sole_id TEXT,
    before_status TEXT,
    after_status TEXT,
    receipt TEXT,
    recipient TEXT,
    message_sha256 TEXT,
    error TEXT,
    seal TEXT NOT NULL
);
