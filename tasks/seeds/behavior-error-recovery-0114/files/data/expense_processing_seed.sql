PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    stable_id TEXT PRIMARY KEY,
    expense_name TEXT NOT NULL,
    location TEXT NOT NULL,
    expense_status TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL
);

CREATE TABLE processing_windows (
    expense_name TEXT NOT NULL,
    location TEXT NOT NULL,
    processing_date TEXT NOT NULL,
    availability TEXT NOT NULL,
    transient_failures INTEGER NOT NULL CHECK (transient_failures >= 0),
    PRIMARY KEY (expense_name, location, processing_date)
);

CREATE TABLE attempts (
    expense_name TEXT NOT NULL,
    location TEXT NOT NULL,
    processing_date TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    PRIMARY KEY (expense_name, location, processing_date)
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO expenses
    (stable_id, expense_name, location, expense_status, amount_cents, currency)
VALUES
    ('exp-114-train', 'Train fare 114', 'Chicago', 'submitted', 4860, 'USD'),
    ('exp-114-lunch', 'Team lunch 114', 'Boston', 'submitted', 17325, 'USD'),
    ('exp-214-train', 'Train fare 114', 'Detroit', 'submitted', 5190, 'USD'),
    ('exp-214-lunch', 'Team lunch 114', 'Cambridge', 'approved', 16840, 'USD');

INSERT INTO processing_windows
    (expense_name, location, processing_date, availability, transient_failures)
VALUES
    ('Train fare 114', 'Chicago', '2026-09-15', 'available', 0),
    ('Team lunch 114', 'Boston', '2026-09-15', 'unavailable', 1),
    ('Train fare 114', 'Detroit', '2026-09-15', 'unavailable', 0),
    ('Team lunch 114', 'Cambridge', '2026-09-15', 'available', 0);
