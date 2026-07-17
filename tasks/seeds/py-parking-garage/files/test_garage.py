"""Acceptance tests for the parking garage kiosk CLI. Run: python3 test_garage.py"""
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


def ok(db, *args):
    p = cli(db, *args)
    assert p.returncode == 0, (args, p.returncode, p.stderr)
    return p.stdout


def fail(db, *args):
    p = cli(db, *args)
    assert p.returncode != 0, (args, p.stdout)
    return p.stderr


def test_pricing_math():
    import pricing

    f = pricing.fee_cents
    # grace period: 15 minutes or less is free, including a zero-length stay
    assert f("2026-05-01T08:00", "2026-05-01T08:00", "standard") == 0
    assert f("2026-05-01T08:00", "2026-05-01T08:15", "standard") == 0
    assert f("2026-05-01T08:00", "2026-05-01T08:15", "oversize") == 0
    # one minute past grace bills a full first hour
    assert f("2026-05-01T08:00", "2026-05-01T08:16", "standard") == 300
    assert f("2026-05-01T08:00", "2026-05-01T08:16", "compact") == 200
    # hour boundaries: any fraction of an hour bills the whole hour
    assert f("2026-05-01T08:00", "2026-05-01T09:00", "standard") == 300
    assert f("2026-05-01T08:00", "2026-05-01T09:01", "standard") == 600
    assert f("2026-05-01T08:00", "2026-05-01T09:30", "oversize") == 1000
    # daily caps kick in
    assert f("2026-05-01T08:00", "2026-05-01T14:00", "standard") == 1800
    assert f("2026-05-01T08:00", "2026-05-01T17:00", "standard") == 1800
    assert f("2026-05-01T08:00", "2026-05-01T15:00", "compact") == 1200
    # midnight is not special
    assert f("2026-05-01T23:50", "2026-05-02T00:10", "standard") == 300
    # exactly 24h is one capped day, one minute more starts a new hour
    assert f("2026-05-01T08:00", "2026-05-02T08:00", "standard") == 1800
    assert f("2026-05-01T08:00", "2026-05-02T08:01", "standard") == 2100
    # multi-day stays: full days at the cap plus a capped remainder
    assert f("2026-05-01T08:00", "2026-05-04T08:00", "standard") == 5400
    assert f("2026-05-01T08:00", "2026-05-03T10:00", "standard") == 4200
    assert f("2026-05-01T06:00", "2026-05-02T12:00", "oversize") == 6000
    # nonsense is rejected, not priced
    for bad in [("2026-05-02T08:00", "2026-05-01T08:00", "standard"),
                ("2026-05-01T08:00", "2026-05-01T09:00", "monster-truck")]:
        try:
            f(*bad)
            assert False, ("expected ValueError", bad)
        except ValueError:
            pass


def test_init_and_errors(tmp):
    db = os.path.join(tmp, "g.json")

    # nothing works before the garage exists
    fail(db, "report")
    fail(db, "enter", "AAA-111", "--size", "compact", "--time", "2026-05-01T08:00")
    assert not os.path.exists(db)

    fail(db, "init", "--compact", "-1", "--standard", "2", "--oversize", "1")
    fail(db, "init", "--compact", "0", "--standard", "0", "--oversize", "0")
    ok(db, "init", "--compact", "2", "--standard", "1", "--oversize", "1")

    # re-init would orphan live tickets: refuse, byte-for-byte untouched
    before = open(db).read()
    fail(db, "init", "--compact", "9", "--standard", "9", "--oversize", "9")
    assert open(db).read() == before

    # garbage in, nonzero out
    fail(db, "enter", "AAA-111", "--size", "bicycle", "--time", "2026-05-01T08:00")
    fail(db, "enter", "AAA-111", "--size", "compact", "--time", "May 1st 8am")
    fail(db, "enter", "AAA-111", "--size", "compact", "--time", "2026-05-01")
    err = fail(db, "exit", "GHOST-1", "--time", "2026-05-01T09:00")
    assert "GHOST-1" in err
    err = fail(db, "find", "GHOST-1")
    assert "GHOST-1" in err

    # db is real JSON on disk
    with open(db) as fh:
        json.load(fh)


def test_spot_assignment(tmp):
    db = os.path.join(tmp, "spots.json")
    ok(db, "init", "--compact", "2", "--standard", "1", "--oversize", "1")

    # lowest-numbered spot in the vehicle's own class first
    out = ok(db, "enter", "CAR-A", "--size", "compact", "--time", "2026-05-01T08:00")
    assert out.strip() == "spot C01", out
    out = ok(db, "enter", "CAR-B", "--size", "compact", "--time", "2026-05-01T08:05")
    assert out.strip() == "spot C02", out

    # same plate can't be inside twice
    err = fail(db, "enter", "CAR-A", "--size", "compact", "--time", "2026-05-01T08:10")
    assert "CAR-A" in err

    # compact class full -> upgrade to standard, then oversize
    out = ok(db, "enter", "CAR-C", "--size", "compact", "--time", "2026-05-01T08:20")
    assert out.strip() == "spot S01", out
    out = ok(db, "enter", "CAR-D", "--size", "compact", "--time", "2026-05-01T08:25")
    assert out.strip() == "spot O01", out

    # garage full for compacts now
    err = fail(db, "enter", "CAR-E", "--size", "compact", "--time", "2026-05-01T08:30")
    assert "compact" in err
    # standard only escalates upward, never down into compact spots
    err = fail(db, "enter", "VAN-A", "--size", "standard", "--time", "2026-05-01T08:31")
    assert "standard" in err
    fail(db, "enter", "RIG-A", "--size", "oversize", "--time", "2026-05-01T08:32")

    # find reports the assigned spot and the entry time as given
    assert ok(db, "find", "CAR-C").strip() == "spot=S01\tsince=2026-05-01T08:20"

    # freeing a standard spot: the next compact prefers C-class, but C is full,
    # so it lands in the freed S01
    ok(db, "exit", "CAR-C", "--time", "2026-05-01T09:00")
    out = ok(db, "enter", "CAR-F", "--size", "compact", "--time", "2026-05-01T09:05")
    assert out.strip() == "spot S01", out

    # freed low-numbered spots are reused first
    ok(db, "exit", "CAR-A", "--time", "2026-05-01T09:10")
    ok(db, "exit", "CAR-B", "--time", "2026-05-01T09:11")
    out = ok(db, "enter", "CAR-G", "--size", "compact", "--time", "2026-05-01T09:15")
    assert out.strip() == "spot C01", out


def test_fees_report_revenue(tmp):
    db = os.path.join(tmp, "rev.json")
    ok(db, "init", "--compact", "2", "--standard", "2", "--oversize", "1")

    ok(db, "enter", "AAA", "--size", "compact", "--time", "2026-05-01T08:00")
    ok(db, "enter", "BBB", "--size", "standard", "--time", "2026-05-01T08:00")
    ok(db, "enter", "CCC", "--size", "oversize", "--time", "2026-05-01T08:00")

    # occupancy is counted per spot class, revenue starts at zero
    assert ok(db, "report").splitlines() == [
        "compact\tused=1/2",
        "standard\tused=1/2",
        "oversize\tused=1/1",
        "revenue=$0.00",
    ]

    # within grace: free, but still leaves
    out = ok(db, "exit", "AAA", "--time", "2026-05-01T08:10")
    assert out.strip() == "fee $0.00", out
    err = fail(db, "find", "AAA")
    assert "AAA" in err

    # a clock that runs backwards is a hardware fault, not a free exit:
    # refuse, and the car is still inside afterwards
    fail(db, "exit", "BBB", "--time", "2026-05-01T07:00")
    assert ok(db, "find", "BBB").strip() == "spot=S01\tsince=2026-05-01T08:00"

    # 2h30m standard -> 3 billable hours
    out = ok(db, "exit", "BBB", "--time", "2026-05-01T10:30")
    assert out.strip() == "fee $9.00", out
    # oversize overnight into the second hour of day two
    out = ok(db, "exit", "CCC", "--time", "2026-05-02T09:01")
    assert out.strip() == "fee $40.00", out

    assert ok(db, "report").splitlines() == [
        "compact\tused=0/2",
        "standard\tused=0/2",
        "oversize\tused=0/1",
        "revenue=$49.00",
    ]

    # a car that upgraded still pays its own class's rate: compact in an
    # oversize spot for 4h is $8.00, not $20.00
    ok(db, "enter", "C1", "--size", "compact", "--time", "2026-05-02T10:00")
    ok(db, "enter", "C2", "--size", "compact", "--time", "2026-05-02T10:00")
    ok(db, "enter", "C3", "--size", "standard", "--time", "2026-05-02T10:00")
    ok(db, "enter", "C4", "--size", "standard", "--time", "2026-05-02T10:00")
    out = ok(db, "enter", "C5", "--size", "compact", "--time", "2026-05-02T10:00")
    assert out.strip() == "spot O01", out
    out = ok(db, "exit", "C5", "--time", "2026-05-02T14:00")
    assert out.strip() == "fee $8.00", out
    assert ok(db, "report").splitlines()[-1] == "revenue=$57.00"


def test_store_roundtrip(tmp):
    import store
    db = os.path.join(tmp, "rt.json")
    ok(db, "init", "--compact", "1", "--standard", "1", "--oversize", "1")
    ok(db, "enter", "RT-1", "--size", "standard", "--time", "2026-05-01T08:00")

    state = store.load(db)
    copy_path = os.path.join(tmp, "rt2.json")
    store.save(copy_path, state)
    assert store.load(copy_path) == state
    # the copy is a fully working db
    assert ok(copy_path, "find", "RT-1").strip() == "spot=S01\tsince=2026-05-01T08:00"


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_pricing_math()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_init_and_errors(tmp)
        test_spot_assignment(tmp)
        test_fees_report_revenue(tmp)
        test_store_roundtrip(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
