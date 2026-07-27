PRAGMA foreign_keys = ON;

CREATE TABLE titles (
    stable_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    branch TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    format TEXT NOT NULL,
    collection TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES titles(stable_id),
    message TEXT NOT NULL
);

INSERT INTO titles
    (stable_id, title, branch, status, record_date, format, collection, lifecycle)
VALUES
    ('lib-293', 'River Almanac, fourth edition', 'Central Branch', 'available',
     '2026-08-09', 'print', 'Circulating', 'current'),
    ('lib-693', 'Quiet Geometry workbook', 'East Branch', 'checked-out',
     '2026-08-10', 'spiral-bound', 'Course support', 'current'),
    ('lib-293-alt', 'River Almanac study guide', 'West Branch', 'archived',
     '2026-08-09', 'print', 'Archive', 'archived'),
    ('lib-319', 'River Almanac, fourth edition', 'West Branch', 'available',
     '2026-08-11', 'print', 'Circulating', 'current'),
    ('lib-427', 'River Almanac fourth edition', 'Central Branch', 'reference-only',
     '2026-08-12', 'print', 'Reference', 'current'),
    ('lib-518', 'River Almanac, fourth edition', 'Central Branch', 'withdrawn',
     '2025-08-09', 'print', 'Archive', 'archived'),
    ('lib-746', 'Quiet Geometry workbook', 'North Branch', 'available',
     '2026-08-10', 'spiral-bound', 'Course support', 'current'),
    ('lib-804', 'Quiet Geometry workbook', 'East Branch', 'withdrawn',
     '2025-08-10', 'spiral-bound', 'Archive', 'archived'),
    ('lib-917', 'Quiet Geometries workbook', 'East Branch', 'available',
     '2026-08-14', 'digital', 'Course support', 'current');
