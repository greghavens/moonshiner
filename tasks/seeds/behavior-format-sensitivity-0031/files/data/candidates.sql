PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    stage TEXT NOT NULL,
    recruiter TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);

INSERT INTO candidates
    (id, full_name, role, stage, recruiter, active)
VALUES
    ('rec-131', 'Nadia Flores', 'Site Reliability Engineer', 'onsite', 'Avery Chen', 1),
    ('rec-531', 'Malcolm Reed', 'Engineering Manager', 'screen', 'Priya Shah', 1),
    ('rec-311', 'Leila Morgan', 'Product Designer', 'offer', 'Jon Bell', 1),
    ('rec-131-archive', 'Nadia Flores', 'Systems Engineer', 'withdrawn', 'Avery Chen', 0);
