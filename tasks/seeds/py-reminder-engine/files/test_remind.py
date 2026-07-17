"""Acceptance tests for the recurring reminder engine. Run: python3 test_remind.py"""
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def remind(store, *args):
    return subprocess.run([sys.executable, "remind.py", "--file", store, *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    store = os.path.join(tmp, "reminders.json")
    try:
        # five reminders, ids handed out in order
        r = remind(store, "add", "rotate api keys", "--rule", "daily",
                   "--interval", "3", "--start", "2026-07-01", "--count", "5")
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout.strip() == "added 1", r.stdout
        r = remind(store, "add", "fire extinguisher check", "--rule", "weekly",
                   "--interval", "2", "--byday", "MO,FR",
                   "--start", "2026-07-01", "--until", "2026-08-01")
        assert r.stdout.strip() == "added 2", (r.stdout, r.stderr)
        r = remind(store, "add", "invoice contractors", "--rule", "monthly",
                   "--start", "2026-01-31", "--count", "4")
        assert r.stdout.strip() == "added 3", (r.stdout, r.stderr)
        r = remind(store, "add", "review backups", "--rule", "monthly",
                   "--start", "2026-06-15", "--until", "2026-09-15")
        assert r.stdout.strip() == "added 4", (r.stdout, r.stderr)
        r = remind(store, "add", "standup prep", "--rule", "daily",
                   "--start", "2026-07-02")
        assert r.stdout.strip() == "added 5", (r.stdout, r.stderr)

        # daily every 3 days, capped by --count
        r = remind(store, "next", "1", "--from", "2026-07-01", "--n", "10")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "2026-07-01", "2026-07-04", "2026-07-07", "2026-07-10",
            "2026-07-13"], r.stdout
        # --from between occurrences snaps forward
        r = remind(store, "next", "1", "--from", "2026-07-05", "--n", "2")
        assert r.stdout.splitlines() == ["2026-07-07", "2026-07-10"], r.stdout

        # weekly on MO,FR every 2nd week; the week holding --start is week 0;
        # nothing lands before the start date itself
        r = remind(store, "next", "2", "--from", "2026-07-01", "--n", "10")
        assert r.stdout.splitlines() == [
            "2026-07-03", "2026-07-13", "2026-07-17", "2026-07-27",
            "2026-07-31"], r.stdout
        # 'next' is on-or-after --from
        r = remind(store, "next", "2", "--from", "2026-07-17", "--n", "3")
        assert r.stdout.splitlines() == [
            "2026-07-17", "2026-07-27", "2026-07-31"], r.stdout

        # monthly on the 31st: short months yield nothing and don't consume --count
        r = remind(store, "next", "3", "--from", "2026-01-01", "--n", "10")
        assert r.stdout.splitlines() == [
            "2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31"], r.stdout

        # --until is inclusive
        r = remind(store, "next", "4", "--from", "2026-06-01", "--n", "10")
        assert r.stdout.splitlines() == [
            "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15"], r.stdout

        # open-ended rule; default --n is 5
        r = remind(store, "next", "5", "--from", "2026-07-02")
        assert r.stdout.splitlines() == [
            "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05",
            "2026-07-06"], r.stdout

        # skipped dates disappear from output but still consume --count
        r = remind(store, "skip", "1", "2026-07-07")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
        r = remind(store, "next", "1", "--from", "2026-07-01", "--n", "10")
        assert r.stdout.splitlines() == [
            "2026-07-01", "2026-07-04", "2026-07-10", "2026-07-13"], r.stdout
        # skipping the same date again is fine
        r = remind(store, "skip", "1", "2026-07-07")
        assert r.returncode == 0, (r.returncode, r.stderr)
        # skipping a date the rule never generates changes nothing
        r = remind(store, "skip", "4", "2026-01-01")
        assert r.returncode == 0, (r.returncode, r.stderr)
        r = remind(store, "next", "4", "--from", "2026-06-01", "--n", "10")
        assert r.stdout.splitlines() == [
            "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15"], r.stdout

        # nothing after the last occurrence
        r = remind(store, "next", "1", "--from", "2026-07-14", "--n", "5")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)

        # agenda: which reminders fire on a given day, ordered by id
        r = remind(store, "agenda", "--on", "2026-07-31")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == [
            "2 fire extinguisher check",
            "3 invoice contractors",
            "5 standup prep"], r.stdout
        # agenda honors skips
        r = remind(store, "agenda", "--on", "2026-07-07")
        assert r.stdout.splitlines() == ["5 standup prep"], r.stdout
        # a day when nothing fires
        r = remind(store, "agenda", "--on", "2026-06-01")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)

        # usage errors: exit 2, message on stderr, store untouched
        with open(store, "rb") as f:
            before = f.read()
        bad_usages = [
            ["add", "x", "--rule", "yearly", "--start", "2026-07-01"],
            ["add", "x", "--rule", "daily", "--start", "2026-07-32"],
            ["add", "x", "--rule", "daily", "--start", "2026-07-01",
             "--count", "0"],
            ["add", "x", "--rule", "daily", "--start", "2026-07-01",
             "--count", "2", "--until", "2026-08-01"],
            ["add", "x", "--rule", "weekly", "--start", "2026-07-01"],
            ["add", "x", "--rule", "weekly", "--byday", "MO,XX",
             "--start", "2026-07-01"],
            ["add", "x", "--rule", "daily", "--byday", "MO",
             "--start", "2026-07-01"],
            ["add", "x", "--rule", "daily", "--start", "2026-07-01",
             "--interval", "0"],
            ["skip", "1", "2026-99-99"],
        ]
        for args in bad_usages:
            r = remind(store, *args)
            assert r.returncode == 2 and r.stderr.strip(), \
                (args, r.returncode, r.stdout, r.stderr)
        with open(store, "rb") as f:
            assert f.read() == before, "usage errors must leave the store untouched"

        # unknown ids: exit 1
        r = remind(store, "next", "99", "--from", "2026-07-01")
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
        r = remind(store, "skip", "99", "2026-07-01")
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)

        # a missing store reads as empty, and read-only commands don't create it
        ghost = os.path.join(tmp, "ghost.json")
        r = remind(ghost, "agenda", "--on", "2026-07-01")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)
        assert not os.path.exists(ghost), "read-only commands must not create the store"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all reminder checks passed")


if __name__ == "__main__":
    main()
