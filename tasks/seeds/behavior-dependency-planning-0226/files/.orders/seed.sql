PRAGMA foreign_keys = ON;

CREATE TABLE order_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL,
    record_date TEXT NOT NULL,
    supplier TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    description TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES order_records(stable_id),
    message TEXT NOT NULL
);

INSERT INTO order_records
    (stable_id, name, city, status, record_date, supplier, amount_cents,
     description, lifecycle)
VALUES
    ('ord-226', 'Lab glassware replenishment', 'Madison', 'awaiting-stock', '2026-07-20', 'North Lake Scientific', 184700, 'Restock request for shared teaching laboratories.', 'current'),
    ('ord-726', 'Conference lanyard order', 'Denver', 'shipped', '2026-07-21', 'Mile High Event Supply', 96200, 'Badge lanyards for the regional conference.', 'current'),
    ('ord-327', 'Lab glassware replenishments', 'Madison', 'approved', '2026-07-19', 'North Lake Scientific', 151000, 'Pluralized glassware request.', 'current'),
    ('ord-428', 'Lab glassware replenishment', 'Milwaukee', 'processing', '2026-07-18', 'Brew City Lab Supply', 179500, 'Similarly named order in another city.', 'current'),
    ('ord-529', 'Lab glassware replenishment', 'Madison', 'received', '2025-07-20', 'North Lake Scientific', 172300, 'Archived exact-name glassware order.', 'archived'),
    ('ord-630', 'Conference lanyard orders', 'Denver', 'draft', '2026-07-22', 'Mile High Event Supply', 101400, 'Pluralized lanyard request.', 'current'),
    ('ord-831', 'Conference lanyard order', 'Boulder', 'approved', '2026-07-20', 'Front Range Events', 84500, 'Similarly named order in another city.', 'current'),
    ('ord-932', 'Conference lanyard order review', 'Denver', 'draft', '2026-07-22', 'Mile High Event Supply', 0, 'Related lanyard planning record.', 'current'),
    ('ord-033', 'Conference lanyard order', 'Denver', 'received', '2025-07-21', 'Mile High Event Supply', 91800, 'Archived exact-name lanyard order.', 'archived'),
    ('ord-134', 'Safety goggle replenishment', 'Madison', 'processing', '2026-07-17', 'North Lake Scientific', 73300, 'Separate laboratory supply order.', 'current');
