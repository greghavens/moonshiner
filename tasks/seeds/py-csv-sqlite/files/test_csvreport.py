"""Acceptance tests for the CSV -> sqlite report tool. Run: python3 test_csvreport.py"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def tool(*args):
    return subprocess.run([sys.executable, "csvreport.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    db = os.path.join(tmp, "sales.db")
    try:
        # import the shipped export
        r = tool("import", "orders.csv", "--db", db)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout.strip() == "imported 12 rows into orders", r.stdout

        # inferred column types land in the sqlite schema
        con = sqlite3.connect(db)
        cols = {row[1]: row[2] for row in con.execute("PRAGMA table_info(orders)")}
        con.close()
        assert cols == {
            "order_id": "INTEGER", "order_date": "TEXT", "region": "TEXT",
            "product": "TEXT", "quantity": "INTEGER", "unit_price": "REAL",
        }, cols

        # re-import replaces the table, never appends
        r = tool("import", "orders.csv", "--db", db)
        assert r.returncode == 0 and r.stdout.strip() == "imported 12 rows into orders", \
            (r.returncode, r.stdout)
        con = sqlite3.connect(db)
        n, = con.execute("SELECT COUNT(*) FROM orders").fetchone()
        con.close()
        assert n == 12, n

        # revenue by region: total desc, order count, tab-separated
        r = tool("report", "by-region", "--db", db)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout.splitlines() == [
            "west\t447.45\t5",
            "east\t392.46\t4",
            "north\t196.17\t3",
        ], r.stdout

        # top products defaults to 3
        r = tool("report", "top-products", "--db", db)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "gadget\t480.00",
            "widget\t339.83",
            "gizmo\t166.75",
        ], r.stdout

        # --limit above the catalog size: quoted product names survive intact
        r = tool("report", "top-products", "--db", db, "--limit", "10")
        assert r.stdout.splitlines() == [
            "gadget\t480.00",
            "widget\t339.83",
            "gizmo\t166.75",
            "deluxe widget, large\t49.50",
        ], r.stdout
        r = tool("report", "top-products", "--db", db, "--limit", "1")
        assert r.stdout.splitlines() == ["gadget\t480.00"], r.stdout

        # monthly buckets, ascending
        r = tool("report", "monthly", "--db", db)
        assert r.stdout.splitlines() == [
            "2026-04\t219.95\t3",
            "2026-05\t428.71\t4",
            "2026-06\t387.42\t5",
        ], r.stdout

        # type inference on a messier file (note the capitalised filename)
        messy = os.path.join(tmp, "Readings.csv")
        with open(messy, "w") as f:
            f.write("sensor,count,ratio,label,spare\n"
                    "a1,3,0.5,7,\n"
                    "a2,-12,2,high,\n"
                    "a3,40,1.25,7b,\n"
                    "a4,,3.5,,\n")
        r = tool("import", messy, "--db", db)
        assert r.returncode == 0 and r.stdout.strip() == "imported 4 rows into readings", \
            (r.returncode, r.stdout, r.stderr)
        con = sqlite3.connect(db)
        cols = {row[1]: row[2] for row in con.execute("PRAGMA table_info(readings)")}
        assert cols == {"sensor": "TEXT", "count": "INTEGER", "ratio": "REAL",
                        "label": "TEXT", "spare": "TEXT"}, cols
        # blanks are NULL; typed columns hold typed values
        row = con.execute(
            "SELECT count, ratio, label, spare FROM readings WHERE sensor='a4'").fetchone()
        assert row == (None, 3.5, None, None), row
        row = con.execute(
            "SELECT count, ratio, label FROM readings WHERE sensor='a2'").fetchone()
        assert row == (-12, 2.0, "high"), row
        # a TEXT column keeps digit-looking values as strings
        val, = con.execute("SELECT label FROM readings WHERE sensor='a1'").fetchone()
        assert val == "7" and isinstance(val, str), val
        # both tables coexist in the one database
        n, = con.execute("SELECT COUNT(*) FROM orders").fetchone()
        assert n == 12, n
        con.close()

        # data errors: exit 1 with a message
        r = tool("import", os.path.join(tmp, "ghost.csv"), "--db", db)
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
        fresh = os.path.join(tmp, "fresh.db")
        r = tool("report", "by-region", "--db", fresh)
        assert r.returncode == 1 and r.stderr.strip(), \
            ("report before any import", r.returncode, r.stderr)
        # usage errors: exit 2
        r = tool("report", "bogus", "--db", db)
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)

        # header-only file: zero rows, all-TEXT schema, keyword headers survive
        hdr = os.path.join(tmp, "audit.csv")
        with open(hdr, "w") as f:
            f.write("who,when,what\n")
        r = tool("import", hdr, "--db", db)
        assert r.returncode == 0 and r.stdout.strip() == "imported 0 rows into audit", \
            (r.returncode, r.stdout, r.stderr)
        con = sqlite3.connect(db)
        cols = {row[1]: row[2] for row in con.execute("PRAGMA table_info(audit)")}
        con.close()
        assert cols == {"who": "TEXT", "when": "TEXT", "what": "TEXT"}, cols
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all csvreport checks passed")


if __name__ == "__main__":
    main()
