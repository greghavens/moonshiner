PRAGMA foreign_keys = ON;

CREATE TABLE inventory_items (
    id TEXT PRIMARY KEY,
    item_name TEXT NOT NULL,
    facility TEXT NOT NULL,
    audit_status TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    current INTEGER NOT NULL CHECK (current IN (0, 1))
);

CREATE TABLE inventory_notes (
    note_id INTEGER PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES inventory_items(id),
    note_kind TEXT NOT NULL,
    body TEXT NOT NULL
);

INSERT INTO inventory_items
    (id, item_name, facility, audit_status, quantity, current)
VALUES
    ('inv-127', 'Torque wrench set', 'North cage', 'audit-due', 14, 1),
    ('inv-527', 'Nitrile glove case', 'Receiving bay', 'replenish', 6, 1),
    ('inv-217', 'Safety glasses carton', 'North cage', 'verified', 38, 1),
    ('inv-127-archive', 'Torque wrench set', 'Legacy depot', 'archived', 11, 0);

INSERT INTO inventory_notes (note_id, item_id, note_kind, body) VALUES
    (1, 'inv-127', 'audit', 'Confirm calibration seals during the next count.'),
    (2, 'inv-527', 'replenishment', 'Replenishment request is awaiting approval.');
