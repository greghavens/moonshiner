PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    account_date TEXT NOT NULL
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    violation INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT
);

INSERT INTO accounts VALUES
('acct_Q7V3M9K2', 'Arbor Foods Renewal', 'West', 'active', '2026-05-21'),
('acct_L4D8P2X6', 'Arbor Foods Renewal', 'Central', 'pending', '2026-10-07'),
('acct_R9C3W7N1', 'Arbor Foods Renewal', 'East', 'closed', '2026-11-12'),
('acct_B6H2T8Y4', 'Arbor Foods Renewal', 'North', 'pending', '2026-12-17'),
('acct_Q7V3M9K3', 'Arbor Foods Renewal — draft', 'West', 'pending', '2026-07-06'),
('acct_Q7V3N9K2', 'Arbor Foods Renewal notes', 'West', 'closed', '2026-08-10'),
('acct_Q7W3M9K2', 'Archive: Arbor Foods Renewal', 'West', 'active', '2026-09-14'),
('acct_Q8V3M9K2', 'Arbor Foods Renewal follow-up', 'West', 'pending', '2026-10-18'),
('acct_P7V3M9K2', 'Arbor Foods Renewal renewal', 'West', 'closed', '2026-11-22'),
('acct_Q7V4M9K2', 'Arbor Foods Renewal briefing', 'West', 'active', '2026-12-26'),
('acct_F2J6S1A5', 'Canyon Labs', 'North', 'active', '2026-04-26'),
('acct_G3K7T2B6', 'Dovetail Arts', 'East', 'closed', '2026-05-08'),
('acct_H4L8U3C7', 'Bright Dental — follow-up', 'Central', 'closed', '2026-06-15'),
('acct_J5M9V4D8', 'Canyon Labs', 'West', 'pending', '2026-07-22'),
('acct_K6N1W5E9', 'Dovetail Arts', 'North', 'active', '2026-08-04'),
('acct_M7P2X6F1', 'Bright Dental — review', 'East', 'closed', '2026-09-11'),
('acct_N8Q3Y7G2', 'Canyon Labs', 'Central', 'closed', '2026-10-18'),
('acct_P9R4Z8H3', 'Dovetail Arts', 'West', 'pending', '2026-11-25'),
('acct_R1S5A9J4', 'Bright Dental — schedule', 'North', 'active', '2026-12-07'),
('acct_S2T6B1K5', 'Canyon Labs', 'East', 'closed', '2026-01-14'),
('acct_T3U7C2L6', 'Dovetail Arts', 'Central', 'closed', '2026-02-21'),
('acct_U4V8D3M7', 'Bright Dental — archive', 'West', 'pending', '2026-03-03'),
('acct_V5W9E4N8', 'Canyon Labs', 'North', 'active', '2026-04-10'),
('acct_W6X1F5P9', 'Dovetail Arts', 'East', 'closed', '2026-05-17'),
('acct_X7Y2G6Q1', 'Bright Dental — briefing', 'Central', 'closed', '2026-06-24'),
('acct_Y8Z3H7R2', 'Canyon Labs', 'West', 'pending', '2026-07-06'),
('acct_Z9A4J8S3', 'Dovetail Arts', 'North', 'active', '2026-08-13'),
('acct_A1B5K9T4', 'Bright Dental — intake', 'East', 'closed', '2026-09-20'),
('acct_B2C6L1U5', 'Canyon Labs', 'Central', 'closed', '2026-10-02'),
('acct_C3D7M2V6', 'Dovetail Arts', 'West', 'pending', '2026-11-09'),
('acct_D4E8N3W7', 'Bright Dental — renewal', 'North', 'active', '2026-12-16'),
('acct_E5F9P4X8', 'Canyon Labs', 'East', 'closed', '2026-01-23'),
('acct_F6G1Q5Y9', 'Dovetail Arts', 'Central', 'closed', '2026-02-05'),
('acct_G7H2R6Z1', 'Bright Dental — reconciliation', 'West', 'pending', '2026-03-12'),
('acct_H8J3S7A2', 'Canyon Labs', 'North', 'active', '2026-04-19'),
('acct_J9K4T8B3', 'Dovetail Arts', 'East', 'closed', '2026-05-26'),
('acct_K1L5U9C4', 'Bright Dental — follow-up', 'Central', 'closed', '2026-06-08'),
('acct_L2M6V1D5', 'Canyon Labs', 'West', 'pending', '2026-07-15'),
('acct_M3N7W2E6', 'Dovetail Arts', 'North', 'active', '2026-08-22'),
('acct_N4P8X3F7', 'Bright Dental — review', 'East', 'closed', '2026-09-04'),
('acct_P5Q9Y4G8', 'Canyon Labs', 'Central', 'closed', '2026-10-11'),
('acct_Q6R1Z5H9', 'Dovetail Arts', 'West', 'pending', '2026-11-18'),
('acct_R7S2A6J1', 'Blue Mesa Milling', 'South', 'active', '2026-01-28'),
('acct_S8T3B7K2', 'Copper Leaf Market', 'West', 'closed', '2026-02-14'),
('acct_T9U4C8L3', 'Evergreen Produce', 'Central', 'active', '2026-03-09'),
('acct_U1V5D9M4', 'Foothill Kitchens', 'East', 'pending', '2026-04-03'),
('acct_V2W6E1N5', 'Golden Field Supply', 'North', 'active', '2026-05-30'),
('acct_W3X7F2P6', 'Highland Cold Storage', 'West', 'closed', '2026-06-19');
