PRAGMA foreign_keys = ON;

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL,
    current INTEGER NOT NULL CHECK (current IN (0, 1))
);

INSERT INTO messages (id, name, location, status, body, current) VALUES
    ('mes-124', 'Renewal reminder 024', 'Volunteers', 'active',
     'Volunteer renewals close Friday at 17:00.', 1),
    ('mes-214', 'Quarterly update 024', 'North Team', 'pending',
     'The quarterly update is awaiting finance review.', 1),
    ('mes-412', 'Renewal reminder archive', 'Volunteers', 'archived',
     'Archived reminder retained for records management.', 0);
