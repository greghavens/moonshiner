PRAGMA foreign_keys = ON;

CREATE TABLE campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    audience TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'ready', 'paused', 'archived')),
    UNIQUE (name, audience)
);

CREATE TABLE availability (
    campaign_name TEXT NOT NULL,
    audience TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (campaign_name, audience, availability_date),
    FOREIGN KEY (campaign_name, audience) REFERENCES campaigns(name, audience)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

INSERT INTO campaigns(id, name, audience, status) VALUES
    ('cam-184', 'Museum donor thank-you', 'Donors', 'ready'),
    ('cam-584', 'Fall enrollment notice', 'Students', 'draft'),
    ('cam-184-alt', 'Museum donor thank-you preview', 'Members', 'paused'),
    ('cam-584-alt', 'Fall enrollment notice review', 'Faculty', 'archived');

INSERT INTO availability(campaign_name, audience, availability_date, available) VALUES
    ('Museum donor thank-you', 'Donors', '2026-09-02', 1),
    ('Fall enrollment notice', 'Students', '2026-09-02', 0),
    ('Museum donor thank-you preview', 'Members', '2026-09-02', 0),
    ('Fall enrollment notice review', 'Faculty', '2026-09-02', 1);
