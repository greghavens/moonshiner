"""Acceptance tests for the expense tracker. Run: python3 test_expenses.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def expenses(*args):
    return subprocess.run([sys.executable, "expenses.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    db = os.path.join(tmp, "ledger.json")
    rules = os.path.join(tmp, "rules.txt")
    write(rules, "starbucks=coffee\n"
                 "whole foods=groceries\n"
                 "trader joe=groceries\n"
                 "shell=fuel\n"
                 "netflix=subscriptions\n"
                 "rent=housing\n")
    try:
        # ---- import the sample bank export
        r = expenses("import", "transactions.csv", "--db", db)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.strip() == "imported 16, skipped 0", r.stdout
        with open(db) as f:
            json.load(f)  # ledger is real JSON

        # importing the same file again is a no-op
        r = expenses("import", "transactions.csv", "--db", db)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.strip() == "imported 0, skipped 16", r.stdout

        # ---- monthly report: categories from rules, first match wins
        r = expenses("report", "--db", db, "--rules", rules, "--month", "2026-05")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "month: 2026-05",
            "income: 2500.00",
            "expenses: 1616.77",
            "net: 883.23",
            "  housing: 1200.00",
            "  groceries: 273.90",
            "  fuel: 85.10",
            "  uncategorized: 32.18",
            "  subscriptions: 15.49",
            "  coffee: 10.10",
        ], r.stdout

        # rule ORDER matters and matching is case-insensitive
        rules2 = os.path.join(tmp, "rules2.txt")
        write(rules2, "WHOLE=warehouse\nwhole foods=groceries\nstarbucks=coffee\n")
        r = expenses("report", "--db", db, "--rules", rules2, "--month", "2026-05")
        assert r.stdout.splitlines() == [
            "month: 2026-05",
            "income: 2500.00",
            "expenses: 1616.77",
            "net: 883.23",
            "  uncategorized: 1396.52",
            "  warehouse: 210.15",
            "  coffee: 10.10",
        ], r.stdout

        # month with no transactions
        r = expenses("report", "--db", db, "--rules", rules, "--month", "2026-01")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "month: 2026-01",
            "income: 0.00",
            "expenses: 0.00",
            "net: 0.00",
        ], r.stdout

        # ---- balance queries
        r = expenses("balance", "--db", db)
        assert r.stdout.strip() == "balance: 5815.13", r.stdout
        r = expenses("balance", "--db", db, "--until", "2026-05-31")
        assert r.stdout.strip() == "balance: 3378.38", r.stdout
        r = expenses("balance", "--db", db, "--until", "2026-04-30")
        assert r.stdout.strip() == "balance: 2495.15", r.stdout

        # balance of a ledger that does not exist yet
        r = expenses("balance", "--db", os.path.join(tmp, "nothing.json"))
        assert r.returncode == 0 and r.stdout.strip() == "balance: 0.00", (r.returncode, r.stdout)

        # ---- duplicates inside one CSV are collapsed too
        db2 = os.path.join(tmp, "second.json")
        twice = os.path.join(tmp, "twice.csv")
        write(twice, "date,description,amount\n"
                     "2026-05-04,Gym Membership,-30.00\n"
                     "2026-05-04,Gym Membership,-30.00\n"
                     "2026-05-06,Farmers Market,-12.50\n")
        r = expenses("import", twice, "--db", db2)
        assert r.stdout.strip() == "imported 2, skipped 1", r.stdout

        # quoted CSV fields with commas survive; negative balances format right
        db3 = os.path.join(tmp, "third.json")
        quoted = os.path.join(tmp, "quoted.csv")
        write(quoted, 'date,description,amount\n2026-05-04,"Fee, wire transfer",-10.00\n')
        r = expenses("import", quoted, "--db", db3)
        assert r.returncode == 0 and r.stdout.strip() == "imported 1, skipped 0", (r.returncode, r.stdout)
        r = expenses("balance", "--db", db3)
        assert r.stdout.strip() == "balance: -10.00", r.stdout

        # ---- a bad row aborts the whole import and changes nothing
        with open(db, "rb") as f:
            before = f.read()
        bad = os.path.join(tmp, "bad.csv")
        write(bad, "date,description,amount\n"
                   "2026-05-07,Legit Coffee,-3.50\n"
                   "2026-05-08,Broken Row,12.x3\n")
        r = expenses("import", bad, "--db", db)
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
        with open(db, "rb") as f:
            assert f.read() == before, "failed import must leave the ledger untouched"
        db4 = os.path.join(tmp, "fourth.json")
        r = expenses("import", bad, "--db", db4)
        assert r.returncode == 1, (r.returncode, r.stderr)
        assert not os.path.exists(db4), "failed import must not create a ledger"

        # bad date is just as fatal
        bad2 = os.path.join(tmp, "bad2.csv")
        write(bad2, "date,description,amount\n2026-13-40,Time Travel,-1.00\n")
        r = expenses("import", bad2, "--db", db4)
        assert r.returncode == 1 and not os.path.exists(db4), (r.returncode, r.stderr)

        # wrong header is rejected
        bad3 = os.path.join(tmp, "bad3.csv")
        write(bad3, "when,what,much\n2026-05-01,x,-1.00\n")
        r = expenses("import", bad3, "--db", db4)
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)

        # ---- missing input files are usage errors (exit 2)
        r = expenses("import", os.path.join(tmp, "ghost.csv"), "--db", db)
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
        r = expenses("report", "--db", db, "--rules", os.path.join(tmp, "ghost.txt"),
                     "--month", "2026-05")
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all expense tracker checks passed")


if __name__ == "__main__":
    main()
