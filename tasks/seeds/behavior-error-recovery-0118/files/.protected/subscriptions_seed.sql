PRAGMA foreign_keys = ON;

CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    group_name TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(plan, group_name)
);

CREATE TABLE availability (
    subscription_id TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (subscription_id, availability_date),
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

CREATE TABLE subscription_preferences (
    subscription_id TEXT PRIMARY KEY,
    renewal_notice TEXT NOT NULL,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

INSERT INTO subscriptions(id, plan, group_name, status) VALUES
    ('sub-fiber-118-family', 'Fiber plan 118', 'Family group', 'active'),
    ('sub-tablet-118-studio', 'Tablet plan 118', 'Studio', 'active'),
    ('sub-fiber-118-studio', 'Fiber plan 118', 'Studio', 'archived'),
    ('sub-tablet-118-family', 'Tablet plan 118', 'Family group', 'paused');

INSERT INTO availability(subscription_id, availability_date, available) VALUES
    ('sub-fiber-118-family', '2026-09-19', 1),
    ('sub-tablet-118-studio', '2026-09-19', 0),
    ('sub-fiber-118-studio', '2026-09-19', 0),
    ('sub-tablet-118-family', '2026-09-19', 1);

INSERT INTO subscription_preferences(subscription_id, renewal_notice) VALUES
    ('sub-fiber-118-family', 'email'),
    ('sub-tablet-118-studio', 'none'),
    ('sub-fiber-118-studio', 'none'),
    ('sub-tablet-118-family', 'postal');
