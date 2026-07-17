"""Acceptance tests for the todo CLI. Run: python3 test_todo.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def todo(*args):
    return subprocess.run([sys.executable, "todo.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    db = os.path.join(tmp, "todos.json")
    try:
        # add prints the new id; ids start at 1
        r = todo("--file", db, "add", "write quarterly report", "--due", "2026-07-10")
        assert r.returncode == 0, f"add failed: rc={r.returncode} stderr={r.stderr!r}"
        assert r.stdout.strip() == "added 1", r.stdout
        r = todo("--file", db, "add", "water the plants")
        assert r.returncode == 0 and r.stdout.strip() == "added 2", (r.returncode, r.stdout)
        r = todo("--file", db, "add", "book flights", "--due", "2026-07-03")
        assert r.stdout.strip() == "added 3", r.stdout
        r = todo("--file", db, "add", "renew passport", "--due", "2026-07-10")
        assert r.stdout.strip() == "added 4", r.stdout

        # the data file is real JSON on disk
        with open(db) as f:
            json.load(f)

        # list: due dates ascending, ties by id, undated last
        r = todo("--file", db, "list")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "3. book flights (due 2026-07-03)",
            "1. write quarterly report (due 2026-07-10)",
            "4. renew passport (due 2026-07-10)",
            "2. water the plants",
        ], r.stdout

        # done is silent, hides the todo from plain list
        r = todo("--file", db, "done", "3")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
        r = todo("--file", db, "list")
        assert r.stdout.splitlines() == [
            "1. write quarterly report (due 2026-07-10)",
            "4. renew passport (due 2026-07-10)",
            "2. water the plants",
        ], r.stdout

        # list --all shows everything with checkbox markers
        r = todo("--file", db, "list", "--all")
        assert r.stdout.splitlines() == [
            "3. [x] book flights (due 2026-07-03)",
            "1. [ ] write quarterly report (due 2026-07-10)",
            "4. [ ] renew passport (due 2026-07-10)",
            "2. [ ] water the plants",
        ], r.stdout

        # domain errors: exit 1 with a message on stderr
        r = todo("--file", db, "done", "3")
        assert r.returncode == 1 and r.stderr.strip(), ("done twice", r.returncode, r.stderr)
        r = todo("--file", db, "done", "99")
        assert r.returncode == 1 and r.stderr.strip(), ("done unknown", r.returncode, r.stderr)
        r = todo("--file", db, "rm", "99")
        assert r.returncode == 1 and r.stderr.strip(), ("rm unknown", r.returncode, r.stderr)

        # rm removes entirely; retired ids are never reused
        r = todo("--file", db, "rm", "4")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)
        r = todo("--file", db, "add", "call the bank")
        assert r.stdout.strip() == "added 5", ("id 4 must stay retired", r.stdout)
        r = todo("--file", db, "list", "--all")
        assert not any(line.startswith("4.") for line in r.stdout.splitlines()), r.stdout

        # usage errors: exit 2, stderr message, data file untouched
        with open(db, "rb") as f:
            before = f.read()
        r = todo("--file", db, "add", "bad date", "--due", "07/10/2026")
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
        r = todo("--file", db, "add", "bad date too", "--due", "2026-13-40")
        assert r.returncode == 2, (r.returncode, r.stderr)
        r = todo("--file", db, "done", "not-a-number")
        assert r.returncode == 2, (r.returncode, r.stderr)
        r = todo("--file", db, "frobnicate")
        assert r.returncode == 2, (r.returncode, r.stderr)
        with open(db, "rb") as f:
            assert f.read() == before, "errors must not modify the data file"

        # empty database: friendly message, file not created by list
        db2 = os.path.join(tmp, "fresh.json")
        r = todo("--file", db2, "list")
        assert r.returncode == 0 and r.stdout.strip() == "no todos", (r.returncode, r.stdout)
        assert not os.path.exists(db2), "list alone must not create the data file"

        # a list where everything is done again shows 'no todos'
        db3 = os.path.join(tmp, "third.json")
        assert todo("--file", db3, "add", "solo task").returncode == 0
        assert todo("--file", db3, "done", "1").returncode == 0
        r = todo("--file", db3, "list")
        assert r.stdout.strip() == "no todos", r.stdout
        r = todo("--file", db3, "list", "--all")
        assert r.stdout.splitlines() == ["1. [x] solo task"], r.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all todo checks passed")


if __name__ == "__main__":
    main()
