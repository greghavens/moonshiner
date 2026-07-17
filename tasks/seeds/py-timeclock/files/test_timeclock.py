"""Acceptance tests for the time-tracking CLI. Run: python3 test_timeclock.py"""
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def cli(db, *args):
    return subprocess.run(
        [sys.executable, "cli.py", "--db", db, *args],
        capture_output=True, text=True, env=ENV, timeout=30)


def ok(db, *args):
    p = cli(db, *args)
    assert p.returncode == 0, (args, p.returncode, p.stderr)
    return p


def main():
    tmp = tempfile.mkdtemp(dir=".")
    db = os.path.join(tmp, "clock.json")
    try:
        # fresh db: idle, empty reports
        p = ok(db, "status")
        assert p.stdout.strip() == "idle", p.stdout
        p = ok(db, "report", "--day", "2026-03-09")
        assert p.stdout == "", p.stdout

        p = ok(db, "start", "alpha", "--at", "2026-03-09 09:00")

        # can't start on top of a running task; message names the culprit
        p = cli(db, "start", "beta", "--at", "2026-03-09 09:30")
        assert p.returncode != 0
        assert "alpha" in p.stderr

        p = ok(db, "status")
        assert p.stdout.strip() == "working on alpha since 2026-03-09 09:00", p.stdout

        # running sessions never appear in reports
        p = ok(db, "report", "--day", "2026-03-09")
        assert p.stdout == "", p.stdout

        # switch stops alpha and starts beta at the same instant
        ok(db, "switch", "beta", "--at", "2026-03-09 10:30")
        p = cli(db, "switch", "beta", "--at", "2026-03-09 10:40")
        assert p.returncode != 0  # already on beta
        ok(db, "stop", "--at", "2026-03-09 11:00")

        # stop/switch with nothing running
        p = cli(db, "stop", "--at", "2026-03-09 11:30")
        assert p.returncode != 0
        p = cli(db, "switch", "gamma", "--at", "2026-03-09 11:30")
        assert p.returncode != 0

        # the logical clock is monotonic; rejected commands leave the db alone
        before = open(db).read()
        p = cli(db, "start", "gamma", "--at", "2026-03-09 08:00")
        assert p.returncode != 0
        p = cli(db, "start", "gamma", "--at", "2026-03-09T08:00")
        assert p.returncode != 0
        p = cli(db, "start", "gamma", "--at", "not a time")
        assert p.returncode != 0
        assert open(db).read() == before

        # a session that crosses midnight
        ok(db, "start", "alpha", "--at", "2026-03-09 23:00")
        ok(db, "stop", "--at", "2026-03-10 01:30")

        # a zero-minute session is legal
        ok(db, "start", "review", "--at", "2026-03-10 09:15")
        ok(db, "stop", "--at", "2026-03-10 09:15")

        # weekend work, still ISO week 11
        ok(db, "start", "beta", "--at", "2026-03-14 10:00")
        ok(db, "stop", "--at", "2026-03-14 11:00")

        # daily report: midnight span clipped to each side
        p = ok(db, "report", "--day", "2026-03-09")
        assert p.stdout.splitlines() == [
            "alpha\t2:30",   # 09:00-10:30 plus 23:00-24:00
            "beta\t0:30",    # 10:30-11:00
        ], p.stdout
        p = ok(db, "report", "--day", "2026-03-10")
        assert p.stdout.splitlines() == [
            "alpha\t1:30",   # 00:00-01:30
            "review\t0:00",  # zero-minute session still listed
        ], p.stdout
        p = ok(db, "report", "--day", "2026-03-11")
        assert p.stdout == "", p.stdout

        # weekly report: Mon 2026-03-09 .. Sun 2026-03-15, any date selects it
        for anchor in ("2026-03-09", "2026-03-11", "2026-03-15"):
            p = ok(db, "report", "--week", anchor)
            assert p.stdout.splitlines() == [
                "alpha\t4:00",
                "beta\t1:30",
                "review\t0:00",
            ], (anchor, p.stdout)

        # a running session in the next week reports nothing
        ok(db, "start", "alpha", "--at", "2026-03-16 08:00")
        p = ok(db, "report", "--week", "2026-03-16")
        assert p.stdout == "", p.stdout
        p = ok(db, "status")
        assert p.stdout.strip() == "working on alpha since 2026-03-16 08:00", p.stdout

        # CSV export: completed sessions only, chronological
        out = os.path.join(tmp, "sessions.csv")
        ok(db, "export", "--csv", out)
        with open(out, newline="") as f:
            rows = [line.rstrip("\r\n") for line in f if line.strip()]
        assert rows == [
            "task,start,end,minutes",
            "alpha,2026-03-09 09:00,2026-03-09 10:30,90",
            "beta,2026-03-09 10:30,2026-03-09 11:00,30",
            "alpha,2026-03-09 23:00,2026-03-10 01:30,150",
            "review,2026-03-10 09:15,2026-03-10 09:15,0",
            "beta,2026-03-14 10:00,2026-03-14 11:00,60",
        ], rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
