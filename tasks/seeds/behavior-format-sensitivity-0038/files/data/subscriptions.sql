PRAGMA foreign_keys = ON;

CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    account_name TEXT NOT NULL,
    plan TEXT NOT NULL,
    status TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    next_billing_date TEXT NOT NULL,
    monthly_price_cents INTEGER NOT NULL,
    line_count INTEGER NOT NULL
);

INSERT INTO subscriptions
    (
        id,
        account_name,
        plan,
        status,
        activated_at,
        next_billing_date,
        monthly_price_cents,
        line_count
    )
VALUES
    (
        'tel-138',
        'Juniper Ridge Design',
        'Business Connect 20GB',
        'active',
        '2024-09-18T14:20:00-06:00',
        '2026-08-01',
        8900,
        3
    ),
    (
        'tel-538',
        'Clearwater Field Services',
        'Enterprise Unlimited',
        'active',
        '2025-02-07T09:15:00-07:00',
        '2026-08-15',
        16400,
        5
    ),
    (
        'tel-318',
        'Mesa Trail Cooperative',
        'Shared Data 10GB',
        'suspended',
        '2023-11-21T16:45:00-07:00',
        '2026-07-28',
        5200,
        2
    ),
    (
        'tel-138-archive',
        'Juniper Ridge Design',
        'Business Voice Legacy',
        'closed',
        '2021-04-02T11:05:00-06:00',
        '2024-09-17',
        6100,
        3
    );
