PRAGMA foreign_keys = ON;

CREATE TABLE inventory (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    storage_note TEXT NOT NULL
);

INSERT INTO inventory
    (id, name, location, status, record_date, quantity, storage_note)
VALUES
    ('inv-147', 'Museum Display Hooks', 'Exhibit Storage', 'in-stock',
     '2026-09-17', 84, 'Acid-free coated hooks for rotating displays.'),
    ('inv-547', 'Archival Tissue Paper', 'Conservation Lab', 'cycle-count',
     '2026-09-18', 12, 'Unbuffered tissue cartons awaiting count confirmation.'),
    ('inv-947', 'Museum Display Hooks', 'Exhibit Workshop', 'discontinued',
     '2025-09-17', 0, 'Workshop-only legacy hardware.'),
    ('inv-847', 'Archival Tissue Paper', 'Conservation Lab Overflow', 'discontinued',
     '2025-09-18', 0, 'Historical overflow stock entry.'),
    ('inv-263', 'Museum Display Hook Labels', 'Exhibit Storage', 'in-stock',
     '2026-08-11', 210, 'Labels are a separate supply item.'),
    ('inv-681', 'Buffered Archival Paper', 'Conservation Lab', 'in-stock',
     '2026-08-12', 29, 'Buffered sheets are not tissue paper.');

CREATE TABLE saved_preferences (
    owner TEXT PRIMARY KEY,
    preference TEXT NOT NULL
);

INSERT INTO saved_preferences (owner, preference)
VALUES ('stockroom-lead', 'compact-location-view');

CREATE TABLE availability (
    location TEXT NOT NULL,
    item_family TEXT NOT NULL,
    replenishment_slots INTEGER NOT NULL,
    PRIMARY KEY (location, item_family)
);

INSERT INTO availability (location, item_family, replenishment_slots)
VALUES
    ('Exhibit Storage', 'display-hardware', 3),
    ('Conservation Lab', 'archival-paper', 1);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    inventory_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    parent_argv_json TEXT NOT NULL,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    violation INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
