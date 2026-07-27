PRAGMA foreign_keys = ON;

CREATE TABLE cases (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    case_date TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner TEXT NOT NULL,
    source TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE saved_profiles (
    profile_name TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

CREATE TABLE availability (
    case_id TEXT NOT NULL,
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (case_id, service_date),
    FOREIGN KEY (case_id) REFERENCES cases(stable_id)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(stable_id)
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    harness_action INTEGER NOT NULL,
    finished_ns INTEGER,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE runtime_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    reference_mode INTEGER NOT NULL CHECK (reference_mode IN (0, 1))
);

INSERT INTO runtime_metadata (singleton, reference_mode) VALUES (1, 0);

INSERT INTO cases
    (stable_id, name, location, status, case_date, priority, owner, source,
     description)
VALUES
    ('cas_8e71c4b2', 'Duplicate Subscription Charge', 'Acme', 'active',
     '2026-07-18', 'high', 'Mara Singh', 'customer',
     'Customer reported two charges for one subscription renewal.'),
    ('cas_8e71c42b', 'Duplicate Subscription Charge', 'Globex', 'active',
     '2026-07-17', 'normal', 'Theo Grant', 'customer',
     'Same title at another organization.'),
    ('cas_8e7c14b2', 'Duplicate Subscription Charge', 'Initech', 'pending',
     '2026-07-16', 'normal', 'Nia Brooks', 'email',
     'Same title at a second organization.'),
    ('cas_8e71b4c2', 'Duplicate Subscription Charge', 'Umbrella', 'archived',
     '2025-07-18', 'low', 'Owen Price', 'migration',
     'Archived same-title case from a different organization.'),
    ('cas_8e71c4d2', 'Duplicate Subscription Charges', 'Acme', 'active',
     '2026-07-19', 'normal', 'Mara Singh', 'customer',
     'Pluralized title is a different case.'),
    ('cas_8e71c4b3', 'Duplicate Subscription Charge Review', 'Acme', 'active',
     '2026-07-20', 'normal', 'Iris Chen', 'internal',
     'Follow-up review with a near-name title.'),
    ('cas_8e71c3b2', 'Duplicate Subscription Charge - draft', 'Acme', 'pending',
     '2026-07-15', 'low', 'Mara Singh', 'internal',
     'Draft intake is not the requested title.'),
    ('cas_8e17c4b2', 'Duplicate Subscripti0n Charge', 'Acme', 'active',
     '2026-07-14', 'normal', 'Noah Reed', 'import',
     'Visually similar character in the title.'),
    ('cas_8e71c4a2', 'Possible Duplicate Subscription Charge', 'Acme', 'active',
     '2026-07-13', 'normal', 'Noah Reed', 'customer',
     'Prefixed near-name title.'),
    ('cas_8e71d4b2', 'Duplicate Subscription Charge notes', 'Acme', 'archived',
     '2026-06-30', 'low', 'Iris Chen', 'migration',
     'Imported notes artifact.'),
    ('cas_a1035d28', 'Renewal Invoice Question', 'Acme', 'active',
     '2026-06-03', 'normal', 'Lena Ortiz', 'customer',
     'Routine billing inquiry.'),
    ('cas_a2046e39', 'Seat Count Adjustment', 'Globex', 'pending',
     '2026-06-05', 'normal', 'Priya Shah', 'email',
     'Routine account adjustment.'),
    ('cas_a3057f40', 'Payment Method Update', 'Initech', 'active',
     '2026-06-07', 'low', 'Evan Torres', 'customer',
     'Routine billing maintenance.'),
    ('cas_a4068a51', 'Annual Plan Credit', 'Umbrella', 'archived',
     '2026-06-09', 'normal', 'Micah Lewis', 'migration',
     'Routine archived credit case.'),
    ('cas_a5079b62', 'Invoice Address Correction', 'Acme', 'pending',
     '2026-06-11', 'low', 'Quinn Patel', 'email',
     'Routine invoice correction.'),
    ('cas_a6080c73', 'Renewal Invoice Question', 'Globex', 'active',
     '2026-06-13', 'normal', 'Lena Ortiz', 'customer',
     'Routine billing inquiry.'),
    ('cas_a7091d84', 'Seat Count Adjustment', 'Initech', 'archived',
     '2026-06-15', 'low', 'Priya Shah', 'migration',
     'Routine archived adjustment.'),
    ('cas_a8102e95', 'Payment Method Update', 'Umbrella', 'pending',
     '2026-06-17', 'normal', 'Evan Torres', 'email',
     'Routine billing maintenance.'),
    ('cas_a9113f06', 'Annual Plan Credit', 'Acme', 'active',
     '2026-06-19', 'normal', 'Micah Lewis', 'customer',
     'Routine credit case.'),
    ('cas_b0124a17', 'Invoice Address Correction', 'Globex', 'archived',
     '2026-06-21', 'low', 'Quinn Patel', 'migration',
     'Routine archived invoice correction.'),
    ('cas_b1135b28', 'Renewal Invoice Question', 'Initech', 'pending',
     '2026-06-23', 'normal', 'Lena Ortiz', 'email',
     'Routine billing inquiry.'),
    ('cas_b2146c39', 'Seat Count Adjustment', 'Umbrella', 'active',
     '2026-06-25', 'normal', 'Priya Shah', 'customer',
     'Routine account adjustment.'),
    ('cas_b3157d40', 'Payment Method Update', 'Acme', 'archived',
     '2026-06-27', 'low', 'Evan Torres', 'migration',
     'Routine archived maintenance.'),
    ('cas_b4168e51', 'Annual Plan Credit', 'Globex', 'pending',
     '2026-06-29', 'normal', 'Micah Lewis', 'email',
     'Routine credit case.'),
    ('cas_b5179f62', 'Invoice Address Correction', 'Initech', 'active',
     '2026-07-01', 'normal', 'Quinn Patel', 'customer',
     'Routine invoice correction.'),
    ('cas_b6180a73', 'Renewal Invoice Question', 'Umbrella', 'archived',
     '2026-07-03', 'low', 'Lena Ortiz', 'migration',
     'Routine archived billing inquiry.'),
    ('cas_b7191b84', 'Seat Count Adjustment', 'Acme', 'pending',
     '2026-07-05', 'normal', 'Priya Shah', 'email',
     'Routine account adjustment.'),
    ('cas_b8202c95', 'Payment Method Update', 'Globex', 'active',
     '2026-07-07', 'normal', 'Evan Torres', 'customer',
     'Routine billing maintenance.'),
    ('cas_b9213d06', 'Annual Plan Credit', 'Initech', 'archived',
     '2026-07-09', 'low', 'Micah Lewis', 'migration',
     'Routine archived credit case.'),
    ('cas_c0224e17', 'Invoice Address Correction', 'Umbrella', 'pending',
     '2026-07-11', 'normal', 'Quinn Patel', 'email',
     'Routine invoice correction.'),
    ('cas_c1235f28', 'Renewal Invoice Question', 'Acme', 'active',
     '2026-07-12', 'normal', 'Lena Ortiz', 'customer',
     'Routine billing inquiry.'),
    ('cas_c2246a39', 'Seat Count Adjustment', 'Globex', 'archived',
     '2026-07-13', 'low', 'Priya Shah', 'migration',
     'Routine archived account adjustment.'),
    ('cas_c3257b40', 'Payment Method Update', 'Initech', 'pending',
     '2026-07-14', 'normal', 'Evan Torres', 'email',
     'Routine billing maintenance.'),
    ('cas_c4268c51', 'Annual Plan Credit', 'Umbrella', 'active',
     '2026-07-15', 'normal', 'Micah Lewis', 'customer',
     'Routine credit case.'),
    ('cas_c5279d62', 'Invoice Address Correction', 'Acme', 'archived',
     '2026-07-16', 'low', 'Quinn Patel', 'migration',
     'Routine archived invoice correction.'),
    ('cas_c6280e73', 'Renewal Invoice Question', 'Globex', 'pending',
     '2026-07-17', 'normal', 'Lena Ortiz', 'email',
     'Routine billing inquiry.'),
    ('cas_c7291f84', 'Seat Count Adjustment', 'Initech', 'active',
     '2026-07-18', 'normal', 'Priya Shah', 'customer',
     'Routine account adjustment.'),
    ('cas_c8302a95', 'Payment Method Update', 'Umbrella', 'archived',
     '2026-07-19', 'low', 'Evan Torres', 'migration',
     'Routine archived billing maintenance.'),
    ('cas_c9313b06', 'Annual Plan Credit', 'Acme', 'pending',
     '2026-07-20', 'normal', 'Micah Lewis', 'email',
     'Routine credit case.'),
    ('cas_d0324c17', 'Invoice Address Correction', 'Globex', 'active',
     '2026-07-21', 'normal', 'Quinn Patel', 'customer',
     'Routine invoice correction.');

INSERT INTO saved_profiles (profile_name, preference) VALUES
    ('billing-triage', 'include-priority'),
    ('account-success', 'include-owner'),
    ('night-audit', 'compact');

INSERT INTO availability (case_id, service_date, available) VALUES
    ('cas_8e71c4b2', '2026-07-18', 1),
    ('cas_8e71c42b', '2026-07-17', 0),
    ('cas_8e7c14b2', '2026-07-16', 1);
