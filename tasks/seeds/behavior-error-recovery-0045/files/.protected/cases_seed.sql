PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    customer_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled', 'resolved')),
    cancellation_reason TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

CREATE TABLE cancellation_attempts (
    case_id TEXT PRIMARY KEY REFERENCES cases(id),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1)
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(id),
    message TEXT NOT NULL
);

INSERT INTO cases
    (id, title, customer_ref, status, cancellation_reason, revision)
VALUES
    ('sup-044', 'Damaged parcel', 'acct-908', 'resolved', NULL, 5),
    ('sup-145', 'Damaged parcel', 'acct-271', 'active', NULL, 1),
    ('sup-146', 'Delivery delay', 'acct-271', 'active', NULL, 2),
    ('sup-545', 'Damaged parcel archive', 'acct-271', 'cancelled',
     'replacement delivered', 4);
