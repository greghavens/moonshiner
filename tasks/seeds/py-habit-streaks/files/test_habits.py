"""Acceptance tests for the habit tracker CLI. Run: python3 test_habits.py"""
import json
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


def report_lines(db, today):
    p = cli(db, "report", "--today", today)
    assert p.returncode == 0, (p.returncode, p.stderr)
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def test_streak_math():
    import habits

    # daily: three consecutive days ending today
    ds = ["2026-03-09", "2026-03-10", "2026-03-11"]
    assert habits.current_streak(ds, "daily", "2026-03-11") == 3
    # today not done yet: streak survives via yesterday
    assert habits.current_streak(ds, "daily", "2026-03-12") == 3
    # a full missed day kills it
    assert habits.current_streak(ds, "daily", "2026-03-13") == 0
    # order and duplicates must not matter
    ds2 = ["2026-03-11", "2026-03-09", "2026-03-10", "2026-03-10"]
    assert habits.current_streak(ds2, "daily", "2026-03-11") == 3
    assert habits.best_streak(ds2, "daily") == 3
    # best run can be in the past
    ds3 = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-10", "2026-03-11"]
    assert habits.best_streak(ds3, "daily") == 3
    assert habits.current_streak(ds3, "daily", "2026-03-11") == 2
    # empty history
    assert habits.current_streak([], "daily", "2026-03-11") == 0
    assert habits.best_streak([], "daily") == 0
    # single completion long ago
    assert habits.current_streak(["2026-01-05"], "daily", "2026-03-11") == 0

    # weekly: completions in ISO weeks 10, 11, 12; today mid week 12
    ws = ["2026-03-05", "2026-03-09", "2026-03-18"]
    assert habits.current_streak(ws, "weekly", "2026-03-18") == 3
    # current week not yet done: previous consecutive weeks still count
    ws2 = ["2026-03-05", "2026-03-09"]          # weeks 10 and 11
    assert habits.current_streak(ws2, "weekly", "2026-03-18") == 2   # week 12
    # a whole missed week kills it
    assert habits.current_streak(ws2, "weekly", "2026-03-23") == 0   # week 13
    # two completions inside one week count as one week
    ws3 = ["2026-03-09", "2026-03-13", "2026-03-15"]  # all ISO week 11
    assert habits.current_streak(ws3, "weekly", "2026-03-14") == 1
    assert habits.best_streak(ws3, "weekly") == 1
    # ISO year boundary: 2025-W52 -> 2026-W01 -> 2026-W02 are consecutive
    yb = ["2025-12-28", "2026-01-04", "2026-01-05"]
    assert habits.current_streak(yb, "weekly", "2026-01-07") == 3
    assert habits.best_streak(yb, "weekly") == 3
    # not "last 7 days": Sun 2026-03-08 (W10) then Mon 2026-03-09 (W11)
    # are one day apart but two distinct consecutive weeks
    wb = ["2026-03-08", "2026-03-09"]
    assert habits.current_streak(wb, "weekly", "2026-03-11") == 2


def test_cli_end_to_end(tmp):
    db = os.path.join(tmp, "habits.json")

    # report before anything exists: silence, success, and no db invented
    assert report_lines(db, "2026-03-11") == []

    p = cli(db, "add", "water", "--schedule", "daily")
    assert p.returncode == 0, p.stderr
    p = cli(db, "add", "review", "--schedule", "weekly")
    assert p.returncode == 0, p.stderr

    # duplicate add fails loudly and leaves the db intact
    before = open(db).read()
    p = cli(db, "add", "water", "--schedule", "daily")
    assert p.returncode != 0
    assert "water" in p.stderr
    assert open(db).read() == before

    # bad schedule rejected
    p = cli(db, "add", "naps", "--schedule", "hourly")
    assert p.returncode != 0

    # unknown habit / malformed date
    p = cli(db, "done", "jogging", "--date", "2026-03-10")
    assert p.returncode != 0
    assert "jogging" in p.stderr
    p = cli(db, "done", "water", "--date", "03/10/2026")
    assert p.returncode != 0

    # completions across separate processes (persistence is the point)
    for d in ["2026-03-09", "2026-03-10", "2026-03-11"]:
        p = cli(db, "done", "water", "--date", d)
        assert p.returncode == 0, p.stderr
    for d in ["2026-03-05", "2026-03-09"]:
        p = cli(db, "done", "review", "--date", d)
        assert p.returncode == 0, p.stderr

    lines = report_lines(db, "2026-03-11")
    assert lines == [
        "review\tweekly\tstreak=2\tbest=2\tdone",
        "water\tdaily\tstreak=3\tbest=3\tdone",
    ], lines

    # next day: water not yet done today -> due, streak still alive
    lines = report_lines(db, "2026-03-12")
    assert lines == [
        "review\tweekly\tstreak=2\tbest=2\tdone",
        "water\tdaily\tstreak=3\tbest=3\tdue",
    ], lines

    # a week later: review missed a week, water long dead
    lines = report_lines(db, "2026-03-23")
    assert lines == [
        "review\tweekly\tstreak=0\tbest=2\tdue",
        "water\tdaily\tstreak=0\tbest=3\tdue",
    ], lines

    # marking done twice on the same date must not double-count
    p = cli(db, "done", "water", "--date", "2026-03-11")
    assert p.returncode == 0, p.stderr
    lines = report_lines(db, "2026-03-12")
    assert "water\tdaily\tstreak=3\tbest=3\tdue" in lines

    # a habit with zero completions reports zeros and is due
    p = cli(db, "add", "stretch", "--schedule", "daily")
    assert p.returncode == 0, p.stderr
    lines = report_lines(db, "2026-03-12")
    assert lines[0] == "review\tweekly\tstreak=2\tbest=2\tdone"
    assert lines[1] == "stretch\tdaily\tstreak=0\tbest=0\tdue"
    assert lines[2] == "water\tdaily\tstreak=3\tbest=3\tdue"

    # db is real JSON on disk
    with open(db) as f:
        json.load(f)


def test_store_roundtrip(tmp):
    import store
    path = os.path.join(tmp, "s.json")
    # a fresh/missing file must load as an empty state, not crash
    state = store.load(path)
    assert state is not None
    store.save(path, state)
    assert store.load(path) == state


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_streak_math()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_cli_end_to_end(tmp)
        test_store_roundtrip(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
