PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    meeting_date TEXT NOT NULL
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    process_group_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    violation INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT
);

INSERT INTO meetings VALUES
('meet_B7K4Q9V2', 'Supplier Policy Review', 'Beacon', 'active', '2026-08-18'),
('meet_H3N8D5R1', 'Supplier Policy Review', 'Harbor', 'active', '2026-08-11'),
('meet_M6T2W7C9', 'Supplier Policy Review', 'Summit', 'cancelled', '2026-08-25'),
('meet_P4X9L3F6', 'Supplier Policy Review', 'Meridian', 'pending', '2026-09-01'),
('meet_B7K4Q9V3', 'Supplier Policy Review - draft', 'Beacon', 'pending', '2026-08-17'),
('meet_B7K4O9V2', 'Supplier Policy Reviews', 'Beacon', 'active', '2026-08-19'),
('meet_B7K4Q9Y2', 'Supplier Policy Review follow-up', 'Beacon', 'active', '2026-08-20'),
('meet_87K4Q9V2', 'Archive: Supplier Policy Review', 'Beacon', 'cancelled', '2026-08-21'),
('meet_B7K4Q9VZ', 'Supplier  Policy Review', 'Beacon', 'pending', '2026-08-22'),
('meet_B7K4G9V2', 'supplier Policy Review', 'Beacon', 'active', '2026-08-23'),
('meet_A2D6J1S5', 'Quarterly Capacity Review', 'Denver', 'active', '2026-04-06'),
('meet_C3F7K2T6', 'Vendor Intake Briefing', 'Beacon', 'pending', '2026-04-13'),
('meet_D4G8L3U7', 'Facilities Safety Review', 'Harbor', 'active', '2026-04-20'),
('meet_E5H9M4W8', 'Benefits Renewal Planning', 'Summit', 'cancelled', '2026-04-27'),
('meet_F6J1N5X9', 'Procurement Standards Council', 'Meridian', 'active', '2026-05-04'),
('meet_G7K2P6Y1', 'Supplier Onboarding Review', 'Beacon', 'pending', '2026-05-11'),
('meet_H8L3Q7Z2', 'Regional Operations Check-in', 'Denver', 'active', '2026-05-18'),
('meet_J9M4R8A3', 'Contract Language Workshop', 'Harbor', 'cancelled', '2026-05-25'),
('meet_K1N5S9B4', 'Inventory Exception Review', 'Summit', 'active', '2026-06-01'),
('meet_L2P6T1C5', 'Security Controls Briefing', 'Meridian', 'pending', '2026-06-08'),
('meet_M3Q7U2D6', 'Travel Policy Calibration', 'Beacon', 'active', '2026-06-15'),
('meet_N4R8V3E7', 'Customer Escalation Review', 'Denver', 'cancelled', '2026-06-22'),
('meet_P5S9W4F8', 'Warehouse Readiness Forum', 'Harbor', 'active', '2026-06-29'),
('meet_Q6T1X5G9', 'Budget Assumptions Review', 'Summit', 'pending', '2026-07-06'),
('meet_R7U2Y6H1', 'Legal Hold Procedures', 'Meridian', 'active', '2026-07-13'),
('meet_S8V3Z7J2', 'Service Provider Review', 'Beacon', 'cancelled', '2026-07-20'),
('meet_T9W4A8K3', 'Maintenance Window Planning', 'Denver', 'active', '2026-07-27'),
('meet_U1X5B9L4', 'Records Retention Workshop', 'Harbor', 'pending', '2026-08-03'),
('meet_V2Y6C1M5', 'Data Classification Review', 'Summit', 'active', '2026-08-10'),
('meet_W3Z7D2N6', 'Partner Risk Roundtable', 'Meridian', 'cancelled', '2026-08-17'),
('meet_X4A8E3P7', 'Purchase Order Controls', 'Beacon', 'active', '2026-08-24'),
('meet_Y5B9F4Q8', 'Incident Response Tabletop', 'Denver', 'pending', '2026-08-31'),
('meet_Z6C1G5R9', 'Quality Metrics Review', 'Harbor', 'active', '2026-09-07'),
('meet_A7D2H6S1', 'Fleet Renewal Planning', 'Summit', 'cancelled', '2026-09-14'),
('meet_B8E3J7T2', 'Audit Evidence Workshop', 'Meridian', 'active', '2026-09-21'),
('meet_C9F4K8U3', 'Supplier Policy Briefing', 'Beacon', 'pending', '2026-09-28'),
('meet_D1G5L9V4', 'Network Capacity Review', 'Denver', 'active', '2026-10-05'),
('meet_E2H6M1W5', 'Invoice Exception Council', 'Harbor', 'cancelled', '2026-10-12'),
('meet_F3J7N2X6', 'Workforce Planning Review', 'Summit', 'active', '2026-10-19'),
('meet_G4K8P3Y7', 'Accessibility Standards Forum', 'Meridian', 'pending', '2026-10-26'),
('meet_H5L9Q4Z8', 'Supply Chain Resilience', 'Beacon', 'active', '2026-11-02'),
('meet_J6M1R5A9', 'Disaster Recovery Review', 'Denver', 'cancelled', '2026-11-09'),
('meet_K7N2S6B1', 'Energy Procurement Update', 'Harbor', 'active', '2026-11-16'),
('meet_L8P3T7C2', 'Privacy Impact Workshop', 'Summit', 'pending', '2026-11-23'),
('meet_M9Q4U8D3', 'Annual Controls Review', 'Meridian', 'active', '2026-11-30'),
('meet_N1R5V9E4', 'Vendor Performance Forum', 'Beacon', 'cancelled', '2026-12-07'),
('meet_P2S6W1F5', 'Hiring Process Review', 'Denver', 'active', '2026-12-14'),
('meet_Q3T7X2G6', 'Insurance Coverage Planning', 'Harbor', 'pending', '2026-12-21'),
('meet_R4U8Y3H7', 'Policy Exception Council', 'Summit', 'active', '2026-12-28'),
('meet_S5V9Z4J8', 'External Audit Kickoff', 'Meridian', 'cancelled', '2027-01-04');
