PRAGMA foreign_keys = ON;

CREATE TABLE title_records (
    stable_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    creator TEXT NOT NULL,
    publication_year INTEGER NOT NULL,
    format TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    edition TEXT NOT NULL
);

INSERT INTO title_records (
    stable_id, title, creator, publication_year, format, location, status, edition
) VALUES
    ('lib-113', 'River Almanac', 'Mara Venn', 2018, 'hardcover', 'Central', 'active', 'Second edition'),
    ('lib-513', 'Quiet Geometry', 'Ilan Roe', 2022, 'ebook', 'East', 'pending', 'First edition'),
    ('lib-913', 'River Almanac', 'Tomas Vale', 1996, 'paperback', 'East', 'withdrawn', 'First edition');
