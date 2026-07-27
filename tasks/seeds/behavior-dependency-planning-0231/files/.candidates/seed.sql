PRAGMA foreign_keys = ON;

CREATE TABLE candidate_records (
    candidate_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    department TEXT NOT NULL,
    status TEXT NOT NULL,
    candidate_date TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    hiring_manager TEXT NOT NULL,
    requisition TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidate_records(candidate_id),
    message TEXT NOT NULL
);

INSERT INTO candidate_records
    (candidate_id, name, role, department, status, candidate_date, email, phone,
     hiring_manager, requisition, notes, lifecycle)
VALUES
    ('cand-231-a7', 'Avery Jones', 'Clinic Scheduler', 'Health Services', 'interview-scheduled', '2026-07-29', 'avery.jones@example.test', '555-0107', 'Morgan Lee', 'HS-1042', 'Panel interview scheduled with clinic operations.', 'current'),
    ('cand-231-b4', 'Jordan Kim', 'Museum Educator', 'Education', 'withdrawn', '2026-06-18', 'jordan.kim@example.test', '555-0144', 'Riley Patel', 'ED-2086', 'Candidate withdrew after accepting another position.', 'current'),
    ('cand-231-c9', 'Avery Jones', 'Clinic Coordinator', 'Health Services', 'screening', '2026-07-25', 'avery.coordinator@example.test', '555-0129', 'Morgan Lee', 'HS-1039', 'Similar candidate name for a different role.', 'current'),
    ('cand-231-d2', 'Avery Jones', 'Clinic Scheduler', 'Community Health', 'offer-review', '2026-07-26', 'avery.community@example.test', '555-0112', 'Sam Ortega', 'CH-3012', 'Same name and role in a different department.', 'current'),
    ('cand-231-e6', 'Avery Jones', 'Clinic Scheduler', 'Health Services', 'not-selected', '2025-11-03', 'avery.archive@example.test', '555-0166', 'Morgan Lee', 'HS-0881', 'Archived application for the same role.', 'archived'),
    ('cand-231-f1', 'Jordan Kim', 'Museum Education Specialist', 'Education', 'interviewing', '2026-07-21', 'jordan.specialist@example.test', '555-0181', 'Riley Patel', 'ED-2091', 'Similar role title in the same department.', 'current'),
    ('cand-231-g8', 'Jordan Kim', 'Museum Educator', 'Public Programs', 'screening', '2026-07-19', 'jordan.programs@example.test', '555-0138', 'Taylor Brooks', 'PP-4108', 'Same name and role in a different department.', 'current'),
    ('cand-231-h5', 'Jordan Kim', 'Museum Educator', 'Education', 'not-selected', '2025-09-14', 'jordan.archive@example.test', '555-0155', 'Riley Patel', 'ED-1740', 'Archived application for the same role.', 'archived'),
    ('cand-231-j3', 'Taylor Morgan', 'Teaching Artist', 'Education', 'offer', '2026-07-20', 'taylor.morgan@example.test', '555-0173', 'Riley Patel', 'ED-2077', 'Separate current education candidate.', 'current');
