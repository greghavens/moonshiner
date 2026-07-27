PRAGMA foreign_keys = ON;

CREATE TABLE facility_requests (
    request_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    assigned_team TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES facility_requests(request_id),
    message TEXT NOT NULL
);

INSERT INTO facility_requests
    (request_id, name, location, status, record_date, category, priority,
     assigned_team, notes, lifecycle)
VALUES
    ('fac-337', 'Clinic air filter replacement', 'Health Center', 'in-progress', '2026-10-29', 'indoor-air', 'high', 'Mechanical Services', 'HEPA bank replacement scheduled around clinic operations.', 'current'),
    ('fac-737', 'Museum gallery repainting', 'Arts Center', 'pending', '2026-10-30', 'finishes', 'normal', 'Facilities Finishes', 'Low-VOC paint specification awaiting final staging window.', 'current'),
    ('fac-137', 'Clinic air filter replacement', 'East Clinic', 'scheduled', '2026-11-02', 'indoor-air', 'normal', 'Mechanical Services', 'Same work name at a different facility.', 'current'),
    ('fac-237', 'Clinic air filters replacement', 'Health Center', 'queued', '2026-10-28', 'indoor-air', 'normal', 'Mechanical Services', 'Pluralized request is a separate work order.', 'current'),
    ('fac-437', 'Clinic air filter replacement', 'Health Center', 'completed', '2025-10-29', 'indoor-air', 'normal', 'Mechanical Services', 'Prior annual request retained for history.', 'archived'),
    ('fac-537', 'Museum gallery repainting', 'History Museum', 'approved', '2026-11-04', 'finishes', 'normal', 'Facilities Finishes', 'Same work name at a different facility.', 'current'),
    ('fac-637', 'Museum gallery painting', 'Arts Center', 'scheduled', '2026-10-31', 'finishes', 'normal', 'Facilities Finishes', 'Similar request without the repainting scope.', 'current'),
    ('fac-837', 'Museum gallery repainting', 'Arts Center', 'completed', '2024-09-16', 'finishes', 'normal', 'Facilities Finishes', 'Archived prior repainting cycle.', 'archived'),
    ('fac-937', 'Loading dock door inspection', 'Civic Center', 'open', '2026-10-24', 'doors', 'low', 'Building Envelope', 'Unrelated current facilities request.', 'current');
