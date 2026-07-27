PRAGMA foreign_keys = ON;

CREATE TABLE claims (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    office TEXT NOT NULL,
    status TEXT NOT NULL,
    claim_date TEXT NOT NULL,
    policyholder TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'archived', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES claims(stable_id),
    message TEXT NOT NULL
);

INSERT INTO claims
    (stable_id, name, office, status, claim_date, policyholder, claim_type,
     lifecycle)
VALUES
    ('ins-296', 'Theft claim — gallery camera', 'West Office',
     'adjuster-assigned', '2026-08-14', 'Juniper Gallery', 'commercial-property',
     'current'),
    ('ins-696', 'Windshield claim — fleet van', 'North Office',
     'documentation-needed', '2026-08-16', 'Northwind Couriers',
     'commercial-auto', 'current'),
    ('ins-184', 'Theft claim — gallery camera', 'North Office',
     'submitted', '2026-08-13', 'Juniper Gallery', 'commercial-property',
     'current'),
    ('ins-296-alt', 'Theft claim — gallery camera inquiry', 'West Office',
     'closed', '2026-08-14', 'Juniper Gallery', 'general-inquiry', 'current'),
    ('ins-337', 'Theft claim - gallery camera', 'West Office',
     'denied', '2026-08-12', 'Juniper Gallery', 'commercial-property',
     'current'),
    ('ins-448', 'Theft claim — gallery camera', 'West Office',
     'closed', '2025-08-14', 'Juniper Gallery', 'commercial-property',
     'archived'),
    ('ins-572', 'Windshield claim — fleet van', 'West Office',
     'approved', '2026-08-15', 'Northwind Couriers', 'commercial-auto',
     'current'),
    ('ins-731', 'Windshield claims — fleet van', 'North Office',
     'submitted', '2026-08-17', 'Northwind Couriers', 'commercial-auto',
     'current'),
    ('ins-845', 'Windshield claim — fleet van', 'North Office',
     'closed', '2025-08-16', 'Northwind Couriers', 'commercial-auto',
     'archived');
