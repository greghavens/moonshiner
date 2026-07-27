PRAGMA foreign_keys = ON;

CREATE TABLE shipments (
    stable_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    shipment_date TEXT NOT NULL,
    carrier TEXT NOT NULL,
    service_level TEXT NOT NULL,
    weight_kg TEXT NOT NULL,
    tracking_class TEXT NOT NULL,
    contact TEXT NOT NULL,
    handling_notes TEXT NOT NULL
);

CREATE TABLE notifications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE execution_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    finished_ns INTEGER,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    violation INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER,
    sole_id TEXT,
    result_digest TEXT,
    error TEXT
);

CREATE TABLE help_inspections (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    inspected_ns INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    parent_pid INTEGER NOT NULL,
    session_id INTEGER NOT NULL
);

INSERT INTO shipments VALUES
('shp_4N8R2C7M', 'Parcel Juniper', 'Portland', 'in_transit', '2026-07-19', 'Northstar Freight', 'priority', '18.40', 'parcel', 'M. Alvarez', 'Keep dry; signature required'),
('shp_4N8R2G7M', 'Parcel Juniper', 'Seattle', 'delivered', '2026-07-12', 'Northstar Freight', 'ground', '16.10', 'parcel', 'D. Chen', 'Reception delivery'),
('shp_4N8R2C7N', 'Parcel Juniper', 'Boise', 'label_created', '2026-07-21', 'Canyon Parcel', 'ground', '20.00', 'parcel', 'R. Singh', 'No weekend delivery'),
('shp_9K3T5V1Q', 'Parcel Juniper', 'Denver', 'exception', '2026-07-17', 'Summit Carrier', 'priority', '15.75', 'parcel', 'J. Price', 'Address review pending'),
('shp_7P2L6D4A', 'Parcel Junipers', 'Portland', 'in_transit', '2026-07-18', 'Northstar Freight', 'priority', '18.40', 'parcel', 'T. Webb', 'Keep dry'),
('shp_7P2L6D4B', 'Parcel-Jupiter', 'Portland', 'delivered', '2026-07-15', 'Canyon Parcel', 'ground', '11.25', 'parcel', 'K. Foster', 'Front desk'),
('shp_7P2L6D4C', 'Parcel Juniper Express', 'Portland', 'label_created', '2026-07-20', 'Northstar Freight', 'express', '9.80', 'parcel', 'A. Bell', 'Call on arrival'),
('shp_7P2L6D4D', 'parcel Juniper', 'Portland', 'cancelled', '2026-07-11', 'Metro Dispatch', 'ground', '17.30', 'parcel', 'S. Ortiz', 'Cancelled by sender'),
('shp_7P2L6D4E', 'Parcel Jupiter', 'Portland', 'in_transit', '2026-07-19', 'Northstar Freight', 'priority', '18.35', 'parcel', 'M. Alvarez', 'Keep upright'),
('shp_7P2L6D4F', 'Juniper Parcel', 'Portland', 'delivered', '2026-07-10', 'Metro Dispatch', 'ground', '18.40', 'parcel', 'L. Young', 'Side entrance'),
('shp_7P2L6D4G', 'Parcel Juniper II', 'Portland', 'exception', '2026-07-16', 'Canyon Parcel', 'priority', '19.00', 'parcel', 'N. Reed', 'Inspect outer carton'),
('shp_1A6H3S8W', 'Crate Alder', 'Portland', 'delivered', '2026-06-30', 'Metro Dispatch', 'freight', '84.00', 'freight', 'C. Myers', 'Forklift required'),
('shp_2B7J4T9X', 'Packet Birch', 'Portland', 'in_transit', '2026-07-18', 'Northstar Freight', 'ground', '2.40', 'packet', 'E. Park', 'Mailbox eligible'),
('shp_3C8K5U1Y', 'Pallet Cedar', 'Portland', 'label_created', '2026-07-22', 'Canyon Parcel', 'freight', '412.00', 'freight', 'F. Brooks', 'Dock appointment'),
('shp_5D9L6V2Z', 'Bundle Dogwood', 'Portland', 'delivered', '2026-07-09', 'Metro Dispatch', 'ground', '23.10', 'bundle', 'G. Hayes', 'No special handling'),
('shp_6E1M7W3R', 'Case Elm', 'Portland', 'exception', '2026-07-14', 'Summit Carrier', 'priority', '31.20', 'case', 'H. Kim', 'Temperature review'),
('shp_8F2N9X4S', 'Carton Fir', 'Portland', 'in_transit', '2026-07-20', 'Northstar Freight', 'ground', '12.80', 'carton', 'I. Ross', 'Stack two high'),
('shp_9G3P1Y5T', 'Envelope Gardenia', 'Portland', 'delivered', '2026-07-13', 'Metro Dispatch', 'express', '0.55', 'document', 'J. Ward', 'Signature waived'),
('shp_1H4Q2Z6U', 'Parcel Hawthorn', 'Portland', 'label_created', '2026-07-23', 'Canyon Parcel', 'priority', '8.65', 'parcel', 'K. Diaz', 'Fragile'),
('shp_2J5R3A7V', 'Crate Iris', 'Portland', 'in_transit', '2026-07-17', 'Summit Carrier', 'freight', '112.00', 'freight', 'L. Stone', 'Keep upright'),
('shp_3K6S4B8W', 'Packet Jasmine', 'Portland', 'delivered', '2026-07-08', 'Metro Dispatch', 'ground', '1.20', 'packet', 'M. Lane', 'Reception delivery'),
('shp_5L7T6C9X', 'Pallet Kalmia', 'Portland', 'exception', '2026-07-15', 'Canyon Parcel', 'freight', '530.00', 'freight', 'N. Cole', 'Hold at terminal'),
('shp_6M8U7D1Y', 'Bundle Lavender', 'Portland', 'in_transit', '2026-07-21', 'Northstar Freight', 'ground', '26.70', 'bundle', 'O. Grant', 'Keep dry'),
('shp_7N9V8E2Z', 'Case Magnolia', 'Portland', 'delivered', '2026-07-07', 'Metro Dispatch', 'priority', '44.10', 'case', 'P. Shah', 'Inside delivery'),
('shp_8P1W9F3R', 'Carton Nettle', 'Portland', 'label_created', '2026-07-24', 'Canyon Parcel', 'ground', '10.35', 'carton', 'Q. Long', 'No weekend delivery'),
('shp_9Q2X1G4S', 'Envelope Orchid', 'Portland', 'in_transit', '2026-07-19', 'Northstar Freight', 'express', '0.75', 'document', 'R. Mills', 'Signature required'),
('shp_1R3Y2H5T', 'Parcel Pine', 'Seattle', 'delivered', '2026-07-06', 'Metro Dispatch', 'ground', '14.30', 'parcel', 'S. Fox', 'Front desk'),
('shp_2S4Z3J6U', 'Crate Quince', 'Boise', 'in_transit', '2026-07-18', 'Canyon Parcel', 'freight', '96.00', 'freight', 'T. Hale', 'Forklift required'),
('shp_3T5A4K7V', 'Packet Rose', 'Denver', 'label_created', '2026-07-25', 'Summit Carrier', 'priority', '1.85', 'packet', 'U. Nash', 'Call on arrival'),
('shp_4U6B5L8W', 'Pallet Spruce', 'Tacoma', 'exception', '2026-07-16', 'Northstar Freight', 'freight', '620.00', 'freight', 'V. Owen', 'Inspect wrapping'),
('shp_5V7C6M9X', 'Bundle Thyme', 'Eugene', 'delivered', '2026-07-05', 'Metro Dispatch', 'ground', '21.90', 'bundle', 'W. Page', 'No special handling'),
('shp_6W8D7N1Y', 'Case Umbrella', 'Salem', 'in_transit', '2026-07-20', 'Canyon Parcel', 'priority', '38.45', 'case', 'X. Quinn', 'Keep upright'),
('shp_7X9E8P2Z', 'Carton Violet', 'Spokane', 'label_created', '2026-07-26', 'Summit Carrier', 'ground', '13.15', 'carton', 'Y. Ray', 'Do not stack'),
('shp_8Y1F9Q3R', 'Envelope Willow', 'Bend', 'delivered', '2026-07-04', 'Metro Dispatch', 'express', '0.65', 'document', 'Z. Scott', 'Signature waived'),
('shp_9Z2G1R4S', 'Parcel Yarrow', 'Seattle', 'in_transit', '2026-07-22', 'Northstar Freight', 'ground', '7.90', 'parcel', 'A. Turner', 'Keep dry'),
('shp_1C3J5M7P', 'Crate Aspen', 'Boise', 'delivered', '2026-06-28', 'Canyon Parcel', 'freight', '140.00', 'freight', 'B. Underwood', 'Dock appointment'),
('shp_2D4K6N8Q', 'Packet Bluebell', 'Denver', 'exception', '2026-07-13', 'Summit Carrier', 'priority', '2.10', 'packet', 'C. Vale', 'Address review pending'),
('shp_3E5L7P9R', 'Pallet Clover', 'Tacoma', 'in_transit', '2026-07-18', 'Northstar Freight', 'freight', '475.00', 'freight', 'D. West', 'Banding intact'),
('shp_4F6M8Q1S', 'Bundle Daffodil', 'Eugene', 'label_created', '2026-07-27', 'Metro Dispatch', 'ground', '19.60', 'bundle', 'E. Xu', 'No weekend delivery'),
('shp_5G7N9R2T', 'Case Echinacea', 'Salem', 'delivered', '2026-07-02', 'Canyon Parcel', 'priority', '35.80', 'case', 'F. York', 'Inside delivery'),
('shp_6H8P1S3U', 'Carton Fern', 'Spokane', 'in_transit', '2026-07-21', 'Summit Carrier', 'ground', '11.40', 'carton', 'G. Zane', 'Stack three high'),
('shp_7J9Q2T4V', 'Envelope Heather', 'Bend', 'delivered', '2026-07-01', 'Metro Dispatch', 'express', '0.80', 'document', 'H. Adams', 'Signature required'),
('shp_8K1R3U5W', 'Parcel Indigo', 'Seattle', 'exception', '2026-07-14', 'Northstar Freight', 'priority', '9.25', 'parcel', 'I. Brown', 'Hold for pickup'),
('shp_9L2S4V6X', 'Crate Laurel', 'Boise', 'in_transit', '2026-07-19', 'Canyon Parcel', 'freight', '128.00', 'freight', 'J. Clark', 'Forklift required'),
('shp_1M3T5W7Y', 'Packet Marigold', 'Denver', 'label_created', '2026-07-28', 'Summit Carrier', 'ground', '1.65', 'packet', 'K. Davis', 'Mailbox eligible'),
('shp_2N4U6X8Z', 'Pallet Olive', 'Tacoma', 'delivered', '2026-06-29', 'Northstar Freight', 'freight', '590.00', 'freight', 'L. Evans', 'Dock delivery'),
('shp_3P5V7Y9A', 'Bundle Peony', 'Eugene', 'in_transit', '2026-07-20', 'Metro Dispatch', 'ground', '24.30', 'bundle', 'M. Green', 'Keep dry'),
('shp_4Q6W8Z1B', 'Case Rowan', 'Salem', 'exception', '2026-07-12', 'Canyon Parcel', 'priority', '41.90', 'case', 'N. Hill', 'Temperature review'),
('shp_5R7X9A2C', 'Carton Sage', 'Spokane', 'delivered', '2026-07-03', 'Summit Carrier', 'ground', '15.20', 'carton', 'O. Irwin', 'Reception delivery'),
('shp_6S8Y1B3D', 'Envelope Tulip', 'Bend', 'in_transit', '2026-07-23', 'Metro Dispatch', 'express', '0.60', 'document', 'P. Jones', 'Signature waived'),
('shp_7T9Z2C4E', 'Parcel Verbena', 'Seattle', 'label_created', '2026-07-29', 'Northstar Freight', 'priority', '12.05', 'parcel', 'Q. King', 'Fragile'),
('shp_8U1A3D5F', 'Crate Wisteria', 'Boise', 'delivered', '2026-06-27', 'Canyon Parcel', 'freight', '105.00', 'freight', 'R. Lewis', 'Keep upright'),
('shp_9V2B4E6G', 'Packet Zinnia', 'Denver', 'in_transit', '2026-07-24', 'Summit Carrier', 'ground', '2.30', 'packet', 'S. Moore', 'Call on arrival');
