PRAGMA foreign_keys = ON;

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    claim_name TEXT NOT NULL,
    office TEXT NOT NULL,
    loss_type TEXT NOT NULL,
    status TEXT NOT NULL,
    claimant TEXT NOT NULL,
    reserve_cents INTEGER NOT NULL CHECK (reserve_cents >= 0),
    assigned_adjuster TEXT,
    UNIQUE(claim_name, office)
);

INSERT INTO claims
    (id, claim_name, office, loss_type, status, claimant, reserve_cents,
     assigned_adjuster)
VALUES
    ('clm-116-theft-west', 'Theft claim 116', 'West Office', 'theft',
     'open', 'Morgan Lee', 840000, 'Casey Nguyen'),
    ('clm-116-glass-north', 'Windshield claim 116', 'North Office',
     'auto glass', 'open', 'Riley Chen', 125000, NULL),
    ('clm-116-theft-north', 'Theft claim 116', 'North Office', 'theft',
     'review', 'Avery Singh', 615000, 'Jordan Silva'),
    ('clm-116-glass-west', 'Windshield claim 116', 'West Office',
     'auto glass', 'closed', 'Taylor Brooks', 97500, 'Sam Okafor');

CREATE TABLE claim_availability (
    claim_name TEXT NOT NULL,
    office TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    availability TEXT NOT NULL,
    slots_available INTEGER NOT NULL CHECK (slots_available >= 0),
    PRIMARY KEY(claim_name, office, availability_date)
);

INSERT INTO claim_availability
    (claim_name, office, availability_date, availability, slots_available)
VALUES
    ('Theft claim 116', 'West Office', '2026-11-17', 'available', 4),
    ('Windshield claim 116', 'North Office', '2026-11-17', 'limited', 1),
    ('Theft claim 116', 'West Office', '2026-11-18', 'unavailable', 0),
    ('Theft claim 116', 'North Office', '2026-11-17', 'limited', 2),
    ('Windshield claim 116', 'West Office', '2026-11-17', 'available', 6);

CREATE TABLE transient_failures (
    claim_name TEXT NOT NULL,
    office TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    PRIMARY KEY(claim_name, office, availability_date)
);

INSERT INTO transient_failures
    (claim_name, office, availability_date, remaining)
VALUES
    ('Windshield claim 116', 'North Office', '2026-11-17', 1);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    outcome TEXT,
    error_code TEXT,
    retryable INTEGER,
    result_digest TEXT,
    seal TEXT
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    claim_id TEXT,
    detail TEXT NOT NULL
);
