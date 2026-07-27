PRAGMA foreign_keys = ON;

CREATE TABLE subscription_items (
    name TEXT NOT NULL,
    account TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    PRIMARY KEY (name, account)
);

CREATE TABLE availability (
    subscription_name TEXT NOT NULL,
    account TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (subscription_name, account, availability_date),
    FOREIGN KEY (subscription_name, account)
        REFERENCES subscription_items(name, account)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY,
    subscription_name TEXT NOT NULL,
    account TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO subscription_items(name, account, owner, status) VALUES
    ('Family fiber subscription', 'Family Account', 'Household Services', 'active'),
    ('Studio tablet data plan', 'Studio Account', 'Creative Operations', 'active'),
    ('Family fiber subscription — legacy', 'Family Account', 'Household Services', 'cancelled'),
    ('Studio tablet data plan', 'Family Account', 'Household Services', 'active');

INSERT INTO availability(subscription_name, account, availability_date, available) VALUES
    ('Family fiber subscription', 'Family Account', '2026-08-03', 1),
    ('Studio tablet data plan', 'Studio Account', '2026-08-03', 0),
    ('Family fiber subscription — legacy', 'Family Account', '2026-08-03', 0),
    ('Studio tablet data plan', 'Family Account', '2026-08-03', 1);
