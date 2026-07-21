PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE saved_preferences (
    preference_key TEXT PRIMARY KEY,
    preference_value TEXT NOT NULL
);

CREATE TABLE availability (
    location TEXT PRIMARY KEY,
    review_slots INTEGER NOT NULL
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO expenses
    (stable_id, name, location, status, expense_date, amount_cents, currency,
     coordinator, notes)
VALUES
    ('exp-2471', 'Chicago Rail Fare', 'Field Programs', 'approved',
     '2026-06-12', 1845, 'USD', 'Avery Chen',
     'Receipt and route documentation attached.'),
    ('exp-6384', 'Boston Team Lunch', 'Operations', 'pending-receipt',
     '2026-06-14', 12680, 'USD', 'Sam Rivera',
     'Itemized receipt requested during exception review.'),
    ('exp-9471', 'Chicago Rail Fare', 'Field Programs Archive', 'closed',
     '2025-06-12', 1675, 'USD', 'Archive Desk',
     'Prior-year entry at a different ledger location.'),
    ('exp-8384', 'Boston Team Lunch', 'Operations Archive', 'closed',
     '2025-06-14', 11940, 'USD', 'Archive Desk',
     'Prior-year entry at a different ledger location.'),
    ('exp-3190', 'Chicago Airport Fare', 'Field Programs', 'approved',
     '2026-06-15', 4520, 'USD', 'Avery Chen',
     'Different expense at the requested location.');

INSERT INTO saved_preferences (preference_key, preference_value) VALUES
    ('default_cost_center', 'Field Programs'),
    ('display_currency', 'USD');

INSERT INTO availability (location, review_slots) VALUES
    ('Field Programs', 2),
    ('Operations', 1);
