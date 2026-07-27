PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    renewal_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'cancelled', 'cancellation-pending', 'archived')
    ),
    cancellation_reason TEXT,
    cancel_requests INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE service_control (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    timeout_after_commit_remaining INTEGER NOT NULL
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    response_kind TEXT,
    observed_status TEXT,
    mutation_applied INTEGER NOT NULL DEFAULT 0,
    violation INTEGER NOT NULL DEFAULT 0
);

INSERT INTO accounts (
    id,
    name,
    region,
    renewal_date,
    status,
    cancellation_reason,
    cancel_requests,
    revision
) VALUES
    (
        'crm-209',
        'Evergreen Clinic renewal',
        'Northwest',
        '2026-11-03',
        'active',
        NULL,
        0,
        1
    ),
    (
        'crm-609',
        'Evergreen Clinic renewal duplicate',
        'Archive',
        '2026-11-03',
        'active',
        NULL,
        0,
        1
    ),
    (
        'crm-481',
        'Highland Pediatrics annual review',
        'Mountain',
        '2026-12-14',
        'active',
        NULL,
        0,
        3
    );

INSERT INTO service_control (singleton, timeout_after_commit_remaining)
VALUES (1, 1);

PRAGMA user_version = 1;
