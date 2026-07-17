"""Shift-log archiving for the quarry weighbridge office."""

import contextlib
import sqlite3


def read_shift(path):
    """Parse one shift file of 'truck,tonnes' lines into (truck, tonnes) rows.

    Blank lines and '#' comments are skipped.
    """
    text = open(path).read()
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        truck, tonnes = ln.split(",")
        rows.append((truck.strip(), float(tonnes)))
    return rows


def last_entries(path, n):
    """The most recent n non-blank lines of a shift file, oldest first."""
    lines = open(path).readlines()
    tail = [ln.strip() for ln in lines if ln.strip()]
    return tail[-n:]


def write_recap(path, day, rows):
    """Write the end-of-shift recap file the foreman signs off."""
    f = open(path, "w")
    f.write("recap %s\n" % day)
    total = 0.0
    for truck, tonnes in rows:
        f.write("%s %.1f\n" % (truck, tonnes))
        total += tonnes
    f.write("total %.1f\n" % total)


def archive_shift(db_path, day, rows):
    """Append a shift's rows to the site archive database."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hauls (day TEXT, truck TEXT, tonnes REAL)"
    )
    conn.executemany(
        "INSERT INTO hauls VALUES (?, ?, ?)",
        [(day, truck, tonnes) for truck, tonnes in rows],
    )
    conn.commit()


def day_total(db_path, day):
    """Total tonnes archived for one day; 0 when the day has no hauls."""
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(tonnes), 0) FROM hauls WHERE day = ?", (day,)
        ).fetchone()
        return row[0]
