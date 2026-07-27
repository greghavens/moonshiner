PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    ship_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    carrier TEXT NOT NULL,
    internal_note TEXT NOT NULL,
    UNIQUE (name, location, ship_date)
);

INSERT INTO shipments
    (id, name, location, ship_date, available, carrier, internal_note)
VALUES
    ('shi-168', 'Library transfer cartons', 'Portland', '2026-08-12', 1,
     'Cascadia Freight', 'Dock appointment confirmed.'),
    ('shi-568', 'Vaccine cooler shipment', 'Denver', '2026-08-12', 1,
     'Front Range Medical Logistics', 'Cold-chain handoff window reserved.'),
    ('shi-168-alt', 'Library transfer cartons return', 'Salem', '2026-08-12', 0,
     'Valley Parcel', 'Return authorization still pending.'),
    ('shi-568-alt', 'Vaccine cooler shipment', 'Aurora', '2026-08-13', 0,
     'Front Range Medical Logistics', 'Alternate lane is not yet scheduled.');

CREATE TABLE operation_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    namespace_id INTEGER NOT NULL,
    outcome TEXT,
    result_count INTEGER,
    result_digest TEXT,
    error TEXT,
    signature TEXT
);
