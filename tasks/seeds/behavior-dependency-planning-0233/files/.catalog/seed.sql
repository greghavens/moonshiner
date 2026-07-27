PRAGMA foreign_keys = ON;

CREATE TABLE title_records (
    title_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    branch TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    call_number TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    notes TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id TEXT NOT NULL REFERENCES title_records(title_id),
    message TEXT NOT NULL
);

INSERT INTO title_records
    (title_id, title, branch, status, record_date, call_number,
     collection_name, notes, lifecycle)
VALUES
    ('title-mb-233', 'Accessible Exhibit Design', 'Museum Branch', 'on-hold', '2026-10-18', 'AM 069.2 ACC', 'Museum Practice', 'Requested for the inclusive galleries workshop.', 'current'),
    ('title-nb-733', 'Night Sky Field Notes', 'North Branch', 'in-transit', '2026-10-19', 'SCI 523.8 NIG', 'Field Science', 'Transferring from the observatory outreach shelf.', 'current'),
    ('title-cb-133', 'Accessible Exhibit Design supplement', 'Central Branch', 'archived', '2026-10-18', 'AM 069.2 ACC SUP', 'Museum Practice', 'Superseded supplemental printing.', 'current'),
    ('title-cb-333', 'Accessible Exhibit Design', 'Central Branch', 'available', '2026-10-16', 'AM 069.2 ACC', 'Museum Practice', 'Same title at another branch.', 'current'),
    ('title-mb-433', 'Accessible Exhibit Designs', 'Museum Branch', 'available', '2026-10-17', 'AM 069.2 ACD', 'Museum Practice', 'Pluralized title is a separate record.', 'current'),
    ('title-mb-533', 'Accessible Exhibit Design', 'Museum Branch', 'withdrawn', '2025-06-11', 'AM 069.2 ACC OLD', 'Museum Practice Archive', 'Archived prior catalog record.', 'archived'),
    ('title-sb-633', 'Night Sky Field Notes', 'South Branch', 'checked-out', '2026-10-20', 'SCI 523.8 NIG', 'Field Science', 'Same title at another branch.', 'current'),
    ('title-nb-833', 'Night-Sky Field Notes', 'North Branch', 'available', '2026-10-15', 'SCI 523.8 NSF', 'Field Science', 'Hyphenated title is a separate record.', 'current'),
    ('title-nb-933', 'Night Sky Field Notes', 'North Branch', 'withdrawn', '2025-04-03', 'SCI 523.8 NIG OLD', 'Field Science Archive', 'Archived prior catalog record.', 'archived'),
    ('title-eb-033', 'Community Oral History Guide', 'East Branch', 'available', '2026-09-28', 'HIS 907.2 COM', 'Local History', 'Unrelated current title.', 'current');
