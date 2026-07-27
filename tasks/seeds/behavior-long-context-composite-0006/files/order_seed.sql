PRAGMA foreign_keys = ON;

CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    order_date TEXT NOT NULL,
    account TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    total_cents INTEGER NOT NULL
);

CREATE TABLE profiles (
    profile_key TEXT PRIMARY KEY,
    profile_value TEXT NOT NULL
);

CREATE TABLE availability (
    sku TEXT NOT NULL,
    location TEXT NOT NULL,
    units INTEGER NOT NULL,
    PRIMARY KEY (sku, location)
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE mutation_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    order_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

INSERT INTO orders
    (id, name, location, status, order_date, account, item_count, total_cents)
VALUES
    ('com-106', 'Ergonomic Chair Order', 'Boise', 'active', '2026-09-21',
     'Boise Workplace Program', 18, 874620),
    ('com-1000', 'Ergonomic Chair Order', 'Phoenix', 'pending', '2026-11-08',
     'Phoenix Office Refresh', 12, 592800),
    ('com-1001', 'Ergonomic Chair Order', 'Denver', 'closed', '2026-12-13',
     'Denver Operations', 8, 395200),
    ('com-1002', 'Ergonomic Chair Order', 'Tulsa', 'pending', '2026-01-18',
     'Tulsa Annex', 10, 494000),
    ('com-1003', 'Ergonomic Chair Order — draft', 'Boise', 'pending', '2026-08-08',
     'Boise Workplace Program', 18, 874620),
    ('com-1004', 'Ergonomic Chair Order notes', 'Boise', 'closed', '2026-09-12',
     'Boise Workplace Program', 2, 98800),
    ('com-1005', 'Archive: Ergonomic Chair Order', 'Boise', 'active', '2026-10-16',
     'Records Migration', 1, 49400),
    ('com-1006', 'Ergonomic Chair Order follow-up', 'Boise', 'pending', '2026-11-20',
     'Boise Workplace Program', 4, 197600),
    ('com-1007', 'Ergonomic Chair Order renewal', 'Boise', 'closed', '2026-12-24',
     'Boise Workplace Program', 6, 296400),
    ('com-1008', 'Ergonomic Chair Order briefing', 'Boise', 'active', '2026-01-03',
     'Facilities Briefing', 1, 49400),
    ('com-1009', 'Supply order', 'Boise', 'active', '2026-05-03',
     'Facilities Stock', 32, 146400),
    ('com-1010', 'Pantry order', 'Tulsa', 'closed', '2026-06-10',
     'Tulsa Hospitality', 24, 81500),
    ('com-1011', 'Gift order — archive', 'Denver', 'closed', '2026-07-17',
     'Denver Events', 7, 119700),
    ('com-1012', 'Supply order', 'Phoenix', 'pending', '2026-08-24',
     'Phoenix Facilities', 28, 134900),
    ('com-1013', 'Pantry order', 'Boise', 'active', '2026-09-06',
     'Boise Hospitality', 30, 92000),
    ('com-1014', 'Gift order — briefing', 'Tulsa', 'closed', '2026-10-13',
     'Tulsa Events', 6, 105000),
    ('com-1015', 'Supply order', 'Denver', 'closed', '2026-11-20',
     'Denver Facilities', 15, 76300),
    ('com-1016', 'Pantry order', 'Phoenix', 'pending', '2026-12-02',
     'Phoenix Hospitality', 20, 68900),
    ('com-1017', 'Gift order — intake', 'Boise', 'active', '2026-01-09',
     'Boise Events', 9, 153000),
    ('com-1018', 'Supply order', 'Tulsa', 'closed', '2026-02-16',
     'Tulsa Facilities', 13, 64800),
    ('com-1019', 'Pantry order', 'Denver', 'closed', '2026-03-23',
     'Denver Hospitality', 17, 57700),
    ('com-1020', 'Gift order — renewal', 'Phoenix', 'pending', '2026-04-05',
     'Phoenix Events', 11, 187000),
    ('com-1021', 'Supply order', 'Boise', 'active', '2026-05-12',
     'Boise Facilities', 34, 158100),
    ('com-1022', 'Pantry order', 'Tulsa', 'closed', '2026-06-19',
     'Tulsa Hospitality', 21, 72500),
    ('com-1023', 'Gift order — reconciliation', 'Denver', 'closed', '2026-07-26',
     'Denver Events', 5, 85000),
    ('com-1024', 'Supply order', 'Phoenix', 'pending', '2026-08-08',
     'Phoenix Facilities', 19, 94700),
    ('com-1025', 'Pantry order', 'Boise', 'active', '2026-09-15',
     'Boise Hospitality', 27, 88100),
    ('com-1026', 'Gift order — follow-up', 'Tulsa', 'closed', '2026-10-22',
     'Tulsa Events', 8, 136000),
    ('com-1027', 'Supply order', 'Denver', 'closed', '2026-11-04',
     'Denver Facilities', 22, 107200),
    ('com-1028', 'Pantry order', 'Phoenix', 'pending', '2026-12-11',
     'Phoenix Hospitality', 16, 54100),
    ('com-1029', 'Gift order — review', 'Boise', 'active', '2026-01-18',
     'Boise Events', 10, 170000),
    ('com-1030', 'Supply order', 'Tulsa', 'closed', '2026-02-25',
     'Tulsa Facilities', 26, 126700),
    ('com-1031', 'Pantry order', 'Denver', 'closed', '2026-03-07',
     'Denver Hospitality', 14, 48700),
    ('com-1032', 'Gift order — schedule', 'Phoenix', 'pending', '2026-04-14',
     'Phoenix Events', 12, 204000),
    ('com-1033', 'Supply order', 'Boise', 'active', '2026-05-21',
     'Boise Facilities', 31, 144200),
    ('com-1034', 'Pantry order', 'Tulsa', 'closed', '2026-06-03',
     'Tulsa Hospitality', 23, 77900),
    ('com-1035', 'Gift order — archive', 'Denver', 'closed', '2026-07-10',
     'Denver Events', 4, 68000),
    ('com-1036', 'Supply order', 'Phoenix', 'pending', '2026-08-17',
     'Phoenix Facilities', 18, 90200),
    ('com-1037', 'Pantry order', 'Boise', 'active', '2026-09-24',
     'Boise Hospitality', 29, 90500),
    ('com-1038', 'Gift order — briefing', 'Tulsa', 'closed', '2026-10-06',
     'Tulsa Events', 7, 119000),
    ('com-1039', 'Supply order', 'Denver', 'closed', '2026-11-13',
     'Denver Facilities', 20, 99200),
    ('com-1040', 'Pantry order', 'Phoenix', 'pending', '2026-12-20',
     'Phoenix Hospitality', 25, 83300);

INSERT INTO profiles (profile_key, profile_value) VALUES
    ('preferred_location', 'Phoenix'),
    ('default_account', 'General Purchasing'),
    ('notification_channel', 'procurement-ops');

INSERT INTO availability (sku, location, units) VALUES
    ('CHAIR-ERG-4', 'Boise', 27),
    ('CHAIR-ERG-4', 'Denver', 11),
    ('DESK-STAND-2', 'Boise', 6);
