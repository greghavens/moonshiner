PRAGMA foreign_keys = ON;

CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    event_date TEXT NOT NULL,
    planner_contact TEXT,
    internal_note TEXT NOT NULL
);

CREATE TABLE saved_profiles (
    profile_name TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

CREATE TABLE availability (
    reservation_id TEXT NOT NULL,
    service_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (reservation_id, service_date),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    finished_ns INTEGER,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

INSERT INTO reservations
    (id, name, location, status, event_date, planner_contact, internal_note)
VALUES
    ('hos-103', 'Maple Hall Planning Retreat', 'Austin', 'active',
     '2026-06-12', 'Elena Park', 'Confirmed directly with venue operations.'),
    ('hos-850', 'Maple Hall Planning Retreat', 'Raleigh', 'pending',
     '2026-08-05', 'Ravi Patel', 'Awaiting deposit review.'),
    ('hos-851', 'Maple Hall Planning Retreat', 'Denver', 'archived',
     '2026-09-10', 'Morgan Diaz', 'Closed market exploration.'),
    ('hos-852', 'Maple Hall Planning Retreat', 'Portland', 'pending',
     '2026-10-15', 'Nia Brooks', 'Tentative venue hold.'),
    ('hos-853', 'Maple Hall Planning Retreat — draft', 'Austin', 'pending',
     '2026-05-05', 'Iris Wong', 'Proposal draft, not a reservation.'),
    ('hos-854', 'Maple Hall Planning Retreat notes', 'Austin', 'archived',
     '2026-06-09', 'Iris Wong', 'Imported notes artifact.'),
    ('hos-855', 'Archive: Maple Hall Planning Retreat', 'Austin', 'archived',
     '2025-07-13', 'Sam Reed', 'Prior-year event archive.'),
    ('hos-856', 'Maple Hall Planning Retreat follow-up', 'Austin', 'pending',
     '2026-08-17', 'Sam Reed', 'Sales follow-up task.'),
    ('hos-857', 'Maple Hall Planning Retreat renewal', 'Austin', 'archived',
     '2026-09-21', 'Sam Reed', 'Renewal campaign artifact.'),
    ('hos-858', 'Maple Hall Planning Retreat briefing', 'Austin', 'active',
     '2026-10-25', 'Sam Reed', 'Internal briefing, not the requested event.'),
    ('hos-013', 'Maple Hall Planner Retreat', 'Austin', 'active',
     '2026-06-13', 'Avery Cole', 'Similar name and number.'),
    ('hos-130', 'Maple Hall Planning Retreat', 'Round Rock', 'active',
     '2026-06-12', 'Avery Cole', 'Similar number and nearby location.'),
    ('hos-310', 'Maple Hall Annual Retreat', 'Austin', 'active',
     '2026-06-12', 'Avery Cole', 'Similar name and date.'),
    ('hos-859', 'Harbor Room', 'Raleigh', 'active',
     '2026-02-25', 'Lee Tran', 'Standard booking.'),
    ('hos-860', 'Juniper Table', 'Austin', 'archived',
     '2026-03-07', 'Lee Tran', 'Standard booking.'),
    ('hos-861', 'Orchid Suite — schedule', 'Portland', 'archived',
     '2026-04-14', 'Lee Tran', 'Schedule artifact.'),
    ('hos-862', 'Harbor Room', 'Denver', 'pending',
     '2026-05-21', 'Lee Tran', 'Standard booking.'),
    ('hos-863', 'Juniper Table', 'Raleigh', 'active',
     '2026-06-03', 'Lee Tran', 'Standard booking.'),
    ('hos-864', 'Orchid Suite — archive', 'Austin', 'archived',
     '2026-07-10', 'Lee Tran', 'Archive artifact.'),
    ('hos-865', 'Harbor Room', 'Portland', 'archived',
     '2026-08-17', 'Lee Tran', 'Standard booking.'),
    ('hos-866', 'Juniper Table', 'Denver', 'pending',
     '2026-09-24', 'Lee Tran', 'Standard booking.'),
    ('hos-867', 'Orchid Suite — briefing', 'Raleigh', 'active',
     '2026-10-06', 'Lee Tran', 'Briefing artifact.'),
    ('hos-868', 'Harbor Room', 'Austin', 'archived',
     '2026-11-13', 'Lee Tran', 'Standard booking.'),
    ('hos-869', 'Juniper Table', 'Portland', 'archived',
     '2026-12-20', 'Lee Tran', 'Standard booking.'),
    ('hos-870', 'Orchid Suite — intake', 'Denver', 'pending',
     '2026-01-02', 'Lee Tran', 'Intake artifact.'),
    ('hos-871', 'Harbor Room', 'Raleigh', 'active',
     '2026-02-09', 'Lee Tran', 'Standard booking.'),
    ('hos-872', 'Juniper Table', 'Austin', 'archived',
     '2026-03-16', 'Lee Tran', 'Standard booking.'),
    ('hos-873', 'Orchid Suite — renewal', 'Portland', 'archived',
     '2026-04-23', 'Lee Tran', 'Renewal artifact.'),
    ('hos-874', 'Harbor Room', 'Denver', 'pending',
     '2026-05-05', 'Lee Tran', 'Standard booking.'),
    ('hos-875', 'Juniper Table', 'Raleigh', 'active',
     '2026-06-12', 'Lee Tran', 'Standard booking.'),
    ('hos-876', 'Orchid Suite — reconciliation', 'Austin', 'archived',
     '2026-07-19', 'Lee Tran', 'Reconciliation artifact.'),
    ('hos-877', 'Harbor Room', 'Portland', 'archived',
     '2026-08-26', 'Lee Tran', 'Standard booking.'),
    ('hos-878', 'Juniper Table', 'Denver', 'pending',
     '2026-09-08', 'Lee Tran', 'Standard booking.'),
    ('hos-879', 'Orchid Suite — follow-up', 'Raleigh', 'active',
     '2026-10-15', 'Lee Tran', 'Follow-up artifact.'),
    ('hos-880', 'Harbor Room', 'Austin', 'archived',
     '2026-11-22', 'Lee Tran', 'Standard booking.'),
    ('hos-881', 'Juniper Table', 'Portland', 'archived',
     '2026-12-04', 'Lee Tran', 'Standard booking.'),
    ('hos-882', 'Orchid Suite — review', 'Denver', 'pending',
     '2026-01-11', 'Lee Tran', 'Review artifact.'),
    ('hos-883', 'Harbor Room', 'Raleigh', 'active',
     '2026-02-18', 'Lee Tran', 'Standard booking.'),
    ('hos-884', 'Juniper Table', 'Austin', 'archived',
     '2026-03-25', 'Lee Tran', 'Standard booking.'),
    ('hos-885', 'Orchid Suite — schedule', 'Portland', 'archived',
     '2026-04-07', 'Lee Tran', 'Schedule artifact.'),
    ('hos-886', 'Harbor Room', 'Denver', 'pending',
     '2026-05-14', 'Lee Tran', 'Standard booking.'),
    ('hos-887', 'Juniper Table', 'Raleigh', 'active',
     '2026-06-21', 'Lee Tran', 'Standard booking.'),
    ('hos-888', 'Orchid Suite — archive', 'Austin', 'archived',
     '2026-07-03', 'Lee Tran', 'Archive artifact.'),
    ('hos-889', 'Harbor Room', 'Portland', 'archived',
     '2026-08-10', 'Lee Tran', 'Standard booking.'),
    ('hos-890', 'Juniper Table', 'Denver', 'pending',
     '2026-09-17', 'Lee Tran', 'Standard booking.');

INSERT INTO saved_profiles (profile_name, preference) VALUES
    ('night-audit', 'compact'),
    ('venue-ops', 'include-internal-notes');

INSERT INTO availability (reservation_id, service_date, available) VALUES
    ('hos-103', '2026-06-12', 1),
    ('hos-850', '2026-08-05', 0),
    ('hos-858', '2026-10-25', 1);
