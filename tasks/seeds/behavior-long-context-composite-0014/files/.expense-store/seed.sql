PRAGMA foreign_keys = ON;

CREATE TABLE expenses (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    incurred_on TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('current', 'stale', 'cancelled'))
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL REFERENCES expenses(stable_id),
    message TEXT NOT NULL
);

INSERT INTO expenses
    (stable_id, name, location, status, amount_cents, incurred_on, lifecycle)
VALUES
    ('exp-114', 'Intercity Rail Fare', 'Chicago', 'active', 6840, '2026-08-22', 'current'),
    ('exp-1400', 'Intercity Rail Fare', 'Boston', 'pending', 7235, '2026-07-16', 'current'),
    ('exp-1401', 'Intercity Rail Fare', 'Denver', 'closed', 6175, '2026-08-21', 'current'),
    ('exp-1402', 'Intercity Rail Fare', 'Seattle', 'pending', 7550, '2026-09-02', 'current'),
    ('exp-1403', 'Intercity Rail Fare — draft', 'Chicago', 'pending', 6710, '2026-04-16', 'current'),
    ('exp-1404', 'Intercity Rail Fare notes', 'Chicago', 'closed', 6840, '2026-05-20', 'current'),
    ('exp-1405', 'Archive: Intercity Rail Fare', 'Chicago', 'active', 5980, '2026-06-24', 'current'),
    ('exp-1406', 'Intercity Rail Fare follow-up', 'Chicago', 'pending', 6925, '2026-07-03', 'current'),
    ('exp-1407', 'Intercity Rail Fare renewal', 'Chicago', 'closed', 7060, '2026-08-07', 'current'),
    ('exp-1408', 'Intercity Rail Fare briefing', 'Chicago', 'active', 7150, '2026-09-11', 'current'),
    ('exp-1409', 'Printing invoice', 'Chicago', 'active', 2495, '2026-01-11', 'current'),
    ('exp-1410', 'Hotel receipt', 'Seattle', 'closed', 18840, '2026-02-18', 'current'),
    ('exp-1411', 'Team lunch — archive', 'Denver', 'closed', 12625, '2026-03-25', 'current'),
    ('exp-1412', 'Printing invoice', 'Boston', 'pending', 2840, '2026-04-07', 'current'),
    ('exp-1413', 'Hotel receipt', 'Chicago', 'active', 21175, '2026-05-14', 'current'),
    ('exp-1414', 'Team lunch — briefing', 'Seattle', 'closed', 13950, '2026-06-21', 'current'),
    ('exp-1415', 'Printing invoice', 'Denver', 'closed', 2635, '2026-07-03', 'current'),
    ('exp-1416', 'Hotel receipt', 'Boston', 'pending', 19480, '2026-08-10', 'current'),
    ('exp-1417', 'Team lunch — intake', 'Chicago', 'active', 14825, '2026-09-17', 'current'),
    ('exp-1418', 'Printing invoice', 'Seattle', 'closed', 3075, '2026-10-24', 'current'),
    ('exp-1419', 'Hotel receipt', 'Denver', 'closed', 22640, '2026-11-06', 'current'),
    ('exp-1420', 'Team lunch — renewal', 'Boston', 'pending', 13280, '2026-12-13', 'current'),
    ('exp-1421', 'Printing invoice', 'Chicago', 'active', 2785, '2026-01-20', 'current'),
    ('exp-1422', 'Hotel receipt', 'Seattle', 'closed', 20150, '2026-02-02', 'current'),
    ('exp-1423', 'Team lunch — reconciliation', 'Denver', 'closed', 14310, '2026-03-09', 'current'),
    ('exp-1424', 'Printing invoice', 'Boston', 'pending', 3190, '2026-04-16', 'current'),
    ('exp-1425', 'Hotel receipt', 'Chicago', 'active', 23475, '2026-05-23', 'current'),
    ('exp-1426', 'Team lunch — follow-up', 'Seattle', 'closed', 15120, '2026-06-05', 'current'),
    ('exp-1427', 'Printing invoice', 'Denver', 'closed', 2925, '2026-07-12', 'current'),
    ('exp-1428', 'Hotel receipt', 'Boston', 'pending', 21760, '2026-08-19', 'current'),
    ('exp-1429', 'Team lunch — review', 'Chicago', 'active', 15940, '2026-09-26', 'current'),
    ('exp-1430', 'Printing invoice', 'Seattle', 'closed', 3345, '2026-10-08', 'current'),
    ('exp-1431', 'Hotel receipt', 'Denver', 'closed', 24280, '2026-11-15', 'current'),
    ('exp-1432', 'Team lunch — schedule', 'Boston', 'pending', 13615, '2026-12-22', 'current'),
    ('exp-1433', 'Printing invoice', 'Chicago', 'active', 3015, '2026-01-04', 'current'),
    ('exp-1434', 'Hotel receipt', 'Seattle', 'closed', 20890, '2026-02-11', 'current'),
    ('exp-1435', 'Team lunch — archive', 'Denver', 'closed', 14725, '2026-03-18', 'current'),
    ('exp-1436', 'Printing invoice', 'Boston', 'pending', 3460, '2026-04-25', 'current'),
    ('exp-1437', 'Hotel receipt', 'Chicago', 'active', 25135, '2026-05-07', 'current'),
    ('exp-1438', 'Team lunch — briefing', 'Seattle', 'closed', 15480, '2026-06-14', 'current'),
    ('exp-1439', 'Printing invoice', 'Denver', 'closed', 3265, '2026-07-21', 'current'),
    ('exp-1440', 'Hotel receipt', 'Boston', 'pending', 22495, '2026-08-03', 'current'),
    ('exp-1441', 'Intercity Rail Fare', 'Chicago', 'closed', 6420, '2025-08-22', 'stale');
