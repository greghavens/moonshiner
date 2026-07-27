PRAGMA foreign_keys = ON;

CREATE TABLE support_cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    current INTEGER NOT NULL CHECK (current IN (0, 1))
);

INSERT INTO support_cases (id, name, account, status, summary, current) VALUES
    ('sup-125', 'Damaged parcel 025', 'Acme', 'active',
     'Outer carton crushed; replacement review is open.', 1),
    ('sup-525', 'Duplicate charge 025', 'Beacon', 'pending',
     'Customer reports two identical card charges.', 1),
    ('sup-215', 'Address correction 025', 'Cedar', 'resolved',
     'Delivery address was corrected before dispatch.', 1),
    ('sup-125-archive', 'Damaged parcel archive', 'Acme', 'archived',
     'Prior damaged-parcel report retained for audit.', 0);
