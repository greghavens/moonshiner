PRAGMA foreign_keys = ON;

CREATE TABLE course_records (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    campus TEXT NOT NULL,
    status TEXT NOT NULL,
    course_date TEXT NOT NULL,
    instructor TEXT NOT NULL,
    room TEXT NOT NULL,
    lifecycle TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO course_records
    (stable_id, name, campus, status, course_date, instructor, room, lifecycle)
VALUES
    (
        'crs-73ad91e4',
        'Microeconomics evening seminar',
        'Downtown Campus',
        'scheduled',
        '2026-09-14',
        'Dr. Lena Torres',
        'DC-214',
        'current'
    ),
    (
        'crs-b8402fc7',
        'Modern history survey',
        'North Campus',
        'confirmed',
        '2026-09-18',
        'Professor Malik Reed',
        'NC-108',
        'current'
    ),
    (
        'crs-draft-92b1',
        'Microeconomics evening seminar',
        'Downtown Campus',
        'draft',
        '2026-10-05',
        'Staff assignment pending',
        'TBD',
        'draft'
    ),
    (
        'crs-draft-61e8',
        'Modern history survey',
        'North Campus',
        'draft',
        '2026-10-09',
        'Staff assignment pending',
        'TBD',
        'draft'
    ),
    (
        'crs-archive-f12c',
        'Microeconomics evening seminar',
        'Downtown Campus',
        'completed',
        '2025-09-15',
        'Dr. Lena Torres',
        'DC-214',
        'archived'
    ),
    (
        'crs-related-4a06',
        'Microeconomics evening seminar workshop',
        'Downtown Campus',
        'scheduled',
        '2026-09-16',
        'Dr. Lena Torres',
        'DC-216',
        'current'
    ),
    (
        'crs-other-7c35',
        'Modern history survey',
        'South Campus',
        'confirmed',
        '2026-09-18',
        'Professor Malik Reed',
        'SC-103',
        'current'
    ),
    (
        'crs-related-b5e2',
        'Modern history survey discussion',
        'North Campus',
        'scheduled',
        '2026-09-19',
        'Professor Malik Reed',
        'NC-110',
        'current'
    );

PRAGMA user_version = 1;
