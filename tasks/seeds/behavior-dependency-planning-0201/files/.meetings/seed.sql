PRAGMA foreign_keys = ON;

CREATE TABLE meetings (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    venue TEXT NOT NULL,
    coordinator TEXT NOT NULL,
    details TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE INDEX meetings_lookup
    ON meetings(name, city, lifecycle, stable_id);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (stable_id) REFERENCES meetings(stable_id)
);

INSERT INTO meetings
    (stable_id, name, city, status, meeting_date, venue, coordinator, details, lifecycle)
VALUES
    ('mtg-118', 'Accessibility Council Check-in', 'Boulder', 'scheduled', '2026-09-02', 'Civic Annex', 'Lena Ortiz', 'Quarterly access review', 'current'),
    ('mtg-317', 'Accessibility Council Check-in', 'Denver', 'completed', '2025-12-04', 'Web conference', 'Mara Chen', 'Archived prior check-in', 'archived'),
    ('mtg-421', 'Accessibility Council Check-in', 'Denver', 'confirmed', '2026-09-03', 'Municipal Center 4B', 'Mara Chen', 'Review public-building access items', 'current'),
    ('mtg-568', 'Accessibility Council Check-in — follow-up', 'Denver', 'draft', '2026-09-05', 'Municipal Center 2A', 'Mara Chen', 'Possible follow-up session', 'current'),
    ('mtg-702', 'Grant Budget Review', 'Denver', 'confirmed', '2026-09-08', 'Finance Room 1', 'Owen Price', 'Regional budget review', 'current'),
    ('mtg-884', 'Grant Budget Review', 'Chicago', 'scheduled', '2026-09-10', 'Grant Office 12', 'Nadia Brooks', 'Review program allocation worksheet', 'current'),
    ('mtg-911', 'Grant Budget Review', 'Chicago', 'completed', '2025-11-19', 'Grant Office 7', 'Nadia Brooks', 'Archived annual review', 'archived'),
    ('mtg-947', 'Grant Budget Review draft', 'Chicago', 'draft', '2026-09-12', 'Grant Office 9', 'Nadia Brooks', 'Unapproved planning draft', 'current');
