PRAGMA foreign_keys = ON;

CREATE TABLE campuses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL
);

CREATE TABLE courses (
    id TEXT PRIMARY KEY,
    campus_id TEXT NOT NULL,
    title TEXT NOT NULL,
    catalog_status TEXT NOT NULL,
    UNIQUE(campus_id, title),
    FOREIGN KEY (campus_id) REFERENCES campuses(id)
);

CREATE TABLE availability (
    course_id TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (course_id, availability_date),
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

INSERT INTO campuses(id, name, status) VALUES
    ('camp-river', 'River Campus', 'active'),
    ('camp-central', 'Central Campus', 'active'),
    ('camp-lakeside', 'Lakeside Campus', 'active');

INSERT INTO courses(id, campus_id, title, catalog_status) VALUES
    ('crs-river-184', 'camp-river', 'Wetland field methods', 'active'),
    ('crs-central-407', 'camp-central', 'Introductory American Sign Language', 'active'),
    ('crs-central-185', 'camp-central', 'Wetland field methods', 'active'),
    ('crs-river-186', 'camp-river', 'Wetland field methods laboratory', 'active'),
    ('crs-river-408', 'camp-river', 'Introductory American Sign Language', 'active'),
    ('crs-central-409', 'camp-central', 'Introductory American Sign Language II', 'active'),
    ('crs-lakeside-410', 'camp-lakeside', 'Introductory American Sign Language', 'archived');

INSERT INTO availability(course_id, availability_date, available) VALUES
    ('crs-river-184', '2026-09-21', 1),
    ('crs-central-407', '2026-09-21', 0),
    ('crs-central-185', '2026-09-21', 0),
    ('crs-river-186', '2026-09-21', 0),
    ('crs-river-408', '2026-09-21', 1),
    ('crs-central-409', '2026-09-21', 1),
    ('crs-lakeside-410', '2026-09-21', 1),
    ('crs-river-184', '2026-09-22', 0),
    ('crs-central-407', '2026-09-22', 1);
