PRAGMA foreign_keys = ON;

CREATE TABLE inventory_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    status TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    bin TEXT NOT NULL,
    description TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES inventory_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO inventory_records
    (stable_id, name, warehouse, status, quantity, bin, description, lifecycle)
VALUES
    ('inv-217', 'Recycled packing tape', 'Warehouse C', 'available', 144, 'C-14', 'Paper-backed packing tape made with recycled fibers.', 'current'),
    ('inv-641', 'A4 printer paper', 'Warehouse D', 'reorder-pending', 12, 'D-08', 'White 80 gsm A4 office paper.', 'current'),
    ('inv-1021', 'Recycled packing tape archive', 'Warehouse C', 'retired', 0, 'C-90', 'Archived related packing tape record.', 'archived'),
    ('inv-328', 'Recycled packaging tape', 'Warehouse C', 'available', 83, 'C-15', 'Similar packaging tape item.', 'current'),
    ('inv-439', 'Recycled packing tape', 'Warehouse B', 'available', 56, 'B-11', 'Same item stocked at another warehouse.', 'current'),
    ('inv-550', 'Recycled packing tape', 'Warehouse C', 'retired', 0, 'C-91', 'Archived exact-name inventory record.', 'archived'),
    ('inv-762', 'A4 recycled printer paper', 'Warehouse D', 'available', 67, 'D-09', 'Related recycled A4 paper item.', 'current'),
    ('inv-873', 'A4 printer paper', 'Warehouse E', 'available', 204, 'E-03', 'Same item stocked at another warehouse.', 'current'),
    ('inv-984', 'A4 printer paper cartons', 'Warehouse D', 'available', 31, 'D-10', 'Carton-sized related paper record.', 'current'),
    ('inv-1095', 'A4 printer paper', 'Warehouse D', 'retired', 0, 'D-92', 'Archived exact-name inventory record.', 'archived'),
    ('inv-106', 'A3 printer paper', 'Warehouse D', 'available', 92, 'D-07', 'Separate A3 office paper item.', 'current');
