PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    stable_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    campus TEXT NOT NULL,
    status TEXT NOT NULL,
    course_date TEXT NOT NULL,
    instructor TEXT NOT NULL,
    schedule TEXT NOT NULL,
    credits INTEGER NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'draft', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES courses(stable_id),
    message TEXT NOT NULL
);

CREATE INDEX course_scope_idx
ON courses(title, campus, lifecycle);

INSERT INTO courses
    (stable_id, title, campus, status, course_date, instructor, schedule, credits, notes, lifecycle)
VALUES
    ('crs-rv-3187', 'Wetland field methods', 'River Campus', 'scheduled', '2026-09-03', 'Dr. Lena Ortiz', 'Thu 08:30-11:20', 4, 'Field kit required', 'current'),
    ('crs-ct-8421', 'Introductory American Sign Language', 'Central Campus', 'registration-open', '2026-08-27', 'Marcel Reed', 'Tue/Thu 14:00-15:15', 3, 'Language lab component', 'current'),
    ('crs-rv-3187-draft', 'Wetland field methods', 'River Campus', 'draft', '2026-09-10', 'Dr. Lena Ortiz', 'Thu 08:30-11:20', 4, 'Superseded planning draft', 'draft'),
    ('crs-ct-2560', 'Wetland field methods', 'Central Campus', 'waitlisted', '2026-09-08', 'Priya Nwosu', 'Mon 09:00-11:50', 4, 'Different campus section', 'current'),
    ('crs-rv-4112', 'Wetland field methods practicum', 'River Campus', 'scheduled', '2026-09-04', 'Dr. Lena Ortiz', 'Fri 08:00-10:50', 2, 'Related practicum', 'current'),
    ('crs-ct-8421-old', 'Introductory American Sign Language', 'Central Campus', 'completed', '2025-08-28', 'Marcel Reed', 'Tue/Thu 14:00-15:15', 3, 'Archived prior offering', 'archived'),
    ('crs-nr-9275', 'Introductory American Sign Language', 'North Campus', 'scheduled', '2026-08-31', 'Dana Cho', 'Mon/Wed 13:00-14:15', 3, 'Different campus section', 'current');

INSERT INTO notifications (stable_id, message)
VALUES ('crs-nr-9275', 'Existing unrelated notification');
