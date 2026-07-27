PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    status TEXT NOT NULL,
    amount TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0, 1))
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    message TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT,
    violation INTEGER NOT NULL DEFAULT 0 CHECK (violation IN (0, 1)),
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER
);

INSERT INTO expenses (
    id, name, location, expense_date, status, amount, submitted_by,
    archived, cancelled
) VALUES
    (
        'EXP-6622', 'Airport shuttle receipt', 'Denver', '2026-08-14',
        'active', '47.25', 'Avery Morgan', 0, 0
    ),
    (
        'EXP-7622', 'Airport shuttle receipt', 'Boulder', '2026-08-14',
        'active', '38.10', 'Avery Morgan', 0, 0
    ),
    (
        'EXP-8622', 'Airport shuttle receipts', 'Denver', '2026-08-15',
        'submitted', '94.50', 'Avery Morgan', 0, 0
    ),
    (
        'EXP-9622', 'Poster printing invoice', 'Portland', '2026-09-19',
        'pending', '61.00', 'Jordan Lee', 0, 0
    ),
    (
        'EXP-10622', 'Airport shuttle receipt', 'Denver', '2025-08-11',
        'archived', '44.00', 'Avery Morgan', 1, 0
    ),
    (
        'EXP-12622', 'Airport shuttle receipt archive', 'Denver', '2026-07-15',
        'archived', '41.80', 'Avery Morgan', 1, 0
    );
