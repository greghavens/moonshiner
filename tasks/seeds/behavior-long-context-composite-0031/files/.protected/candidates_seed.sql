PRAGMA foreign_keys = ON;

CREATE TABLE candidates (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team TEXT NOT NULL,
    location TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    role TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    source TEXT NOT NULL,
    recruiter TEXT NOT NULL,
    current_stage TEXT NOT NULL,
    recommended_next_step TEXT NOT NULL
);

CREATE TABLE candidate_actions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    action TEXT NOT NULL,
    value TEXT NOT NULL
);

CREATE TABLE interviews (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    scheduled_for TEXT NOT NULL
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
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
    returned_ids_json TEXT,
    result_digest TEXT,
    error TEXT
);

INSERT INTO candidates VALUES
('cand_7H2M9Q4V', 'Casey Evans', 'Sales', 'Denver', 'active', 'Enterprise Account Executive', 'casey.evans@example.com', '+1 303-555-0148', '2026-07-08T14:35:00-06:00', 'Employee referral', 'Morgan Lee', 'Hiring manager review', 'Schedule a 45-minute hiring manager interview'),
('cand_7H2M9Q5V', 'Casey Evans', 'Customer Success', 'Denver', 'active', 'Customer Success Manager', 'casey.evans.cs@example.com', '+1 303-555-0124', '2026-07-05T09:20:00-06:00', 'Agency', 'Taylor Kim', 'Recruiter screen', 'Request portfolio review'),
('cand_7H2N9Q4V', 'Casey Evans', 'Sales', 'Denver', 'withdrawn', 'Sales Development Representative', 'casey.evans.sdr@example.com', '+1 720-555-0173', '2026-06-11T11:10:00-06:00', 'Careers site', 'Morgan Lee', 'Closed', 'No next step'),
('cand_6H2M9Q4V', 'Casey Evans', 'Sales', 'Austin', 'active', 'Mid-Market Account Executive', 'casey.evans.austin@example.com', '+1 512-555-0119', '2026-07-02T10:05:00-05:00', 'Sourced', 'Alex Rivera', 'Panel interview', 'Collect panel feedback'),
('cand_8H2M9Q4V', 'Casey Evans', 'Marketing', 'Seattle', 'rejected', 'Demand Generation Manager', 'casey.evans.seattle@example.com', '+1 206-555-0161', '2026-05-19T15:45:00-07:00', 'Careers site', 'Jamie Chen', 'Closed', 'No next step'),
('cand_7G2M9Q4V', 'Casey Evans', 'Sales', 'Chicago', 'hired', 'Regional Sales Director', 'casey.evans.chicago@example.com', '+1 312-555-0190', '2025-12-03T08:40:00-06:00', 'Executive search', 'Morgan Lee', 'Hired', 'Begin onboarding'),
('cand_7H2M8Q4V', 'Casey Evans', 'Finance', 'Denver', 'archived', 'Revenue Accountant', 'casey.evans.finance@example.com', '+1 303-555-0186', '2025-10-27T13:15:00-06:00', 'University event', 'Sam Patel', 'Archived', 'No next step'),
('cand_7H2M9P4V', 'Casey Evan', 'Sales', 'Denver', 'active', 'Account Executive', 'casey.evan@example.com', '+1 303-555-0106', '2026-07-10T16:25:00-06:00', 'Referral', 'Morgan Lee', 'Recruiter screen', 'Complete recruiter screen'),
('cand_7H2M9Q4W', 'Casey Evans-Smith', 'Sales', 'Denver', 'active', 'Account Executive', 'casey.evans-smith@example.com', '+1 720-555-0137', '2026-07-12T12:30:00-06:00', 'Careers site', 'Morgan Lee', 'Application review', 'Review application'),
('cand_1A5D8K3R', 'Jordan Blake', 'Engineering', 'Denver', 'active', 'Platform Engineer', 'jordan.blake@example.com', '+1 303-555-0150', '2026-06-21T09:00:00-06:00', 'Sourced', 'Jamie Chen', 'Technical screen', 'Schedule technical interview'),
('cand_2B6E9L4S', 'Riley Morgan', 'Sales', 'Denver', 'active', 'Sales Operations Analyst', 'riley.morgan@example.com', '+1 720-555-0108', '2026-06-24T10:30:00-06:00', 'Careers site', 'Morgan Lee', 'Recruiter screen', 'Complete recruiter screen'),
('cand_3C7F1M5T', 'Avery Stone', 'People', 'Austin', 'rejected', 'People Partner', 'avery.stone@example.com', '+1 512-555-0142', '2026-05-18T11:20:00-05:00', 'Referral', 'Taylor Kim', 'Closed', 'No next step'),
('cand_4D8G2N6U', 'Cameron Reed', 'Marketing', 'Seattle', 'active', 'Content Strategist', 'cameron.reed@example.com', '+1 206-555-0133', '2026-07-01T14:10:00-07:00', 'Careers site', 'Sam Patel', 'Portfolio review', 'Collect writing samples'),
('cand_5E9H3P7V', 'Quinn Parker', 'Finance', 'Chicago', 'withdrawn', 'Financial Analyst', 'quinn.parker@example.com', '+1 312-555-0177', '2026-04-08T08:50:00-05:00', 'Agency', 'Alex Rivera', 'Closed', 'No next step'),
('cand_6F1J4Q8W', 'Skyler Hayes', 'Customer Success', 'Denver', 'active', 'Implementation Manager', 'skyler.hayes@example.com', '+1 303-555-0195', '2026-07-03T15:05:00-06:00', 'Employee referral', 'Taylor Kim', 'Hiring manager review', 'Request interview availability'),
('cand_7G2K5R9X', 'Emerson Cole', 'Sales', 'Austin', 'active', 'Account Executive', 'emerson.cole@example.com', '+1 512-555-0184', '2026-06-29T12:45:00-05:00', 'Sourced', 'Morgan Lee', 'Panel interview', 'Collect panel feedback'),
('cand_8H3L6S1Y', 'Drew Bennett', 'Engineering', 'Seattle', 'hired', 'Security Engineer', 'drew.bennett@example.com', '+1 206-555-0112', '2026-02-12T09:35:00-08:00', 'Conference', 'Jamie Chen', 'Hired', 'Begin onboarding'),
('cand_9J4M7T2Z', 'Peyton Brooks', 'Marketing', 'Chicago', 'archived', 'Product Marketing Manager', 'peyton.brooks@example.com', '+1 312-555-0159', '2025-11-14T16:00:00-06:00', 'Agency', 'Sam Patel', 'Archived', 'No next step'),
('cand_1K5N8U3A', 'Rowan Price', 'Sales', 'Denver', 'rejected', 'Sales Engineer', 'rowan.price@example.com', '+1 720-555-0165', '2026-05-30T13:25:00-06:00', 'Careers site', 'Morgan Lee', 'Closed', 'No next step'),
('cand_2L6P9V4B', 'Finley Shaw', 'Legal', 'Austin', 'active', 'Commercial Counsel', 'finley.shaw@example.com', '+1 512-555-0127', '2026-07-06T10:15:00-05:00', 'Referral', 'Alex Rivera', 'Hiring manager review', 'Schedule legal team interview'),
('cand_3M7Q1W5C', 'Harper Lane', 'Customer Success', 'Seattle', 'active', 'Support Operations Lead', 'harper.lane@example.com', '+1 206-555-0188', '2026-06-26T11:55:00-07:00', 'Sourced', 'Taylor Kim', 'Technical exercise', 'Review exercise'),
('cand_4N8R2X6D', 'Reese Carter', 'Finance', 'Chicago', 'withdrawn', 'Senior Accountant', 'reese.carter@example.com', '+1 312-555-0136', '2026-04-19T09:45:00-05:00', 'Careers site', 'Sam Patel', 'Closed', 'No next step'),
('cand_5P9S3Y7E', 'Kendall Ross', 'Engineering', 'Denver', 'active', 'Data Engineer', 'kendall.ross@example.com', '+1 303-555-0171', '2026-07-07T14:40:00-06:00', 'Employee referral', 'Jamie Chen', 'Technical screen', 'Schedule pair programming interview'),
('cand_6Q1T4Z8F', 'Dakota Gray', 'Sales', 'Austin', 'active', 'Business Development Manager', 'dakota.gray@example.com', '+1 512-555-0155', '2026-07-09T08:30:00-05:00', 'Sourced', 'Morgan Lee', 'Recruiter screen', 'Complete recruiter screen'),
('cand_7R2U5A9G', 'Sage Turner', 'People', 'Seattle', 'rejected', 'Recruiting Coordinator', 'sage.turner@example.com', '+1 206-555-0146', '2026-05-02T15:20:00-07:00', 'University event', 'Taylor Kim', 'Closed', 'No next step'),
('cand_8S3V6B1H', 'Alexis Wood', 'Marketing', 'Chicago', 'active', 'Marketing Operations Manager', 'alexis.wood@example.com', '+1 312-555-0198', '2026-07-04T12:10:00-05:00', 'Agency', 'Sam Patel', 'Panel interview', 'Collect panel feedback'),
('cand_9T4W7C2J', 'Micah Bell', 'Customer Success', 'Denver', 'archived', 'Technical Account Manager', 'micah.bell@example.com', '+1 720-555-0115', '2025-09-16T10:50:00-06:00', 'Referral', 'Alex Rivera', 'Archived', 'No next step'),
('cand_1U5X8D3K', 'Charlie Young', 'Engineering', 'Austin', 'active', 'Frontend Engineer', 'charlie.young@example.com', '+1 512-555-0168', '2026-07-11T13:00:00-05:00', 'Careers site', 'Jamie Chen', 'Technical exercise', 'Review exercise'),
('cand_2V6Y9E4L', 'Marley Scott', 'Sales', 'Seattle', 'hired', 'Account Executive', 'marley.scott@example.com', '+1 206-555-0120', '2026-01-23T09:10:00-08:00', 'Employee referral', 'Morgan Lee', 'Hired', 'Begin onboarding'),
('cand_3W7Z1F5M', 'Robin Ward', 'Finance', 'Chicago', 'active', 'FP&A Manager', 'robin.ward@example.com', '+1 312-555-0144', '2026-06-30T16:35:00-05:00', 'Executive search', 'Sam Patel', 'Hiring manager review', 'Schedule finance panel');
