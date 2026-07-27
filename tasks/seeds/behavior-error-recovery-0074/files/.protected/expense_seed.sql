PRAGMA foreign_keys = ON;

CREATE TABLE expense_items (
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    PRIMARY KEY (name, city)
);

CREATE TABLE availability (
    expense_name TEXT NOT NULL,
    city TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (expense_name, city, availability_date),
    FOREIGN KEY (expense_name, city) REFERENCES expense_items(name, city)
);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_name TEXT NOT NULL,
    city TEXT NOT NULL,
    availability_date TEXT NOT NULL,
    message TEXT NOT NULL
);

INSERT INTO expense_items(name, city, owner, status) VALUES
  ('Chicago rail fare — outreach trip', 'Chicago', 'Community Programs', 'active'),
  ('Boston team lunch — budget workshop', 'Boston', 'Finance Enablement', 'active'),
  ('Chicago rail fare — workshop trip', 'Chicago', 'Learning Programs', 'active'),
  ('Boston team lunch — outreach workshop', 'Boston', 'Community Programs', 'active');

INSERT INTO availability(expense_name, city, availability_date, available) VALUES
  ('Chicago rail fare — outreach trip', 'Chicago', '2026-08-03', 1),
  ('Boston team lunch — budget workshop', 'Boston', '2026-08-03', 0),
  ('Chicago rail fare — workshop trip', 'Chicago', '2026-08-03', 0),
  ('Boston team lunch — outreach workshop', 'Boston', '2026-08-03', 1),
  ('Chicago rail fare — outreach trip', 'Chicago', '2026-08-04', 0),
  ('Boston team lunch — budget workshop', 'Boston', '2026-08-04', 1);
