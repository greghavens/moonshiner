"""Acceptance tests for the library lending CLI. Run: python3 test_library.py"""
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


def test_lending_math():
    import lending

    assert lending.due_date("2026-04-01") == "2026-04-15"
    # month boundary
    assert lending.due_date("2026-01-25") == "2026-02-08"
    # early and on-time returns owe nothing
    assert lending.late_fee_cents("2026-02-08", "2026-02-01") == 0
    assert lending.late_fee_cents("2026-02-08", "2026-02-08") == 0
    # a quarter per late day
    assert lending.late_fee_cents("2026-02-08", "2026-02-09") == 25
    assert lending.late_fee_cents("2026-02-08", "2026-03-01") == 21 * 25


def test_catalog_and_holds(tmp):
    db = os.path.join(tmp, "lib.json")
    ok(db, "add-book", "978-1", "--title", "Dune", "--copies", "1")
    ok(db, "add-book", "978-2", "--title", "Emma", "--copies", "2")

    # duplicate isbn fails loudly and leaves the db intact
    before = open(db).read()
    err = fail(db, "add-book", "978-1", "--title", "Dune again", "--copies", "3")
    assert "978-1" in err
    assert open(db).read() == before

    # a book with zero copies makes no sense
    fail(db, "add-book", "978-3", "--title", "Ghost", "--copies", "0")

    # unknown isbn everywhere
    err = fail(db, "checkout", "978-9", "--member", "ana", "--date", "2026-04-01")
    assert "978-9" in err
    fail(db, "status", "978-9")
    fail(db, "hold", "978-9", "--member", "ana")

    out = ok(db, "checkout", "978-1", "--member", "ana", "--date", "2026-04-01")
    assert out.strip() == "due 2026-04-15", out
    # same member cannot have two copies of the same book
    fail(db, "checkout", "978-1", "--member", "ana", "--date", "2026-04-02")
    assert ok(db, "status", "978-1").strip() == "Dune\tavailable=0/1\tholds=0"
    assert ok(db, "status", "978-2").strip() == "Emma\tavailable=2/2\tholds=0"

    # holds: no holding what you could just check out, no double-queueing,
    # no holding a book you already have out
    fail(db, "hold", "978-2", "--member", "bo")
    ok(db, "hold", "978-1", "--member", "bo")
    ok(db, "hold", "978-1", "--member", "cy")
    fail(db, "hold", "978-1", "--member", "bo")
    fail(db, "hold", "978-1", "--member", "ana")
    assert ok(db, "status", "978-1").strip() == "Dune\tavailable=0/1\tholds=2"

    # nothing available: everyone bounces
    fail(db, "checkout", "978-1", "--member", "dee", "--date", "2026-04-03")

    out = ok(db, "return", "978-1", "--member", "ana", "--date", "2026-04-05")
    assert out.strip() == "fine $0.00", out
    assert ok(db, "status", "978-1").strip() == "Dune\tavailable=1/1\tholds=2"

    # the freed copy belongs to the head of the queue, nobody else
    fail(db, "checkout", "978-1", "--member", "dee", "--date", "2026-04-06")
    fail(db, "checkout", "978-1", "--member", "cy", "--date", "2026-04-06")
    out = ok(db, "checkout", "978-1", "--member", "bo", "--date", "2026-04-06")
    assert out.strip() == "due 2026-04-20", out
    assert ok(db, "status", "978-1").strip() == "Dune\tavailable=0/1\tholds=1"

    # queue advanced: cy is first now
    ok(db, "return", "978-1", "--member", "bo", "--date", "2026-04-07")
    ok(db, "checkout", "978-1", "--member", "cy", "--date", "2026-04-07")
    assert ok(db, "status", "978-1").strip() == "Dune\tavailable=0/1\tholds=0"

    # returning a book you don't have
    fail(db, "return", "978-2", "--member", "ana", "--date", "2026-04-08")


def test_fines_and_blocking(tmp):
    db = os.path.join(tmp, "fines.json")
    ok(db, "add-book", "111", "--title", "Kim", "--copies", "1")
    ok(db, "add-book", "222", "--title", "Ada", "--copies", "1")
    ok(db, "add-book", "333", "--title", "Oz", "--copies", "1")

    ok(db, "checkout", "111", "--member", "max", "--date", "2026-04-01")
    out = ok(db, "member", "max")
    assert out.splitlines() == ["111\tdue=2026-04-15", "fines=$0.00"], out

    # 20 days late -> $5.00 -> checkout privileges suspended
    out = ok(db, "return", "111", "--member", "max", "--date", "2026-05-05")
    assert out.strip() == "fine $5.00", out
    assert ok(db, "member", "max").splitlines() == ["fines=$5.00"]
    err = fail(db, "checkout", "222", "--member", "max", "--date", "2026-05-06")
    assert "max" in err

    # paying down below the threshold restores privileges
    fail(db, "pay", "max", "--amount", "9.00")   # more than owed
    fail(db, "pay", "max", "--amount", "0")
    ok(db, "pay", "max", "--amount", "1.00")
    assert ok(db, "member", "max").splitlines() == ["fines=$4.00"]
    ok(db, "checkout", "222", "--member", "max", "--date", "2026-05-06")

    # member report: loans sorted by isbn, fines last
    ok(db, "checkout", "111", "--member", "max", "--date", "2026-05-06")
    out = ok(db, "member", "max")
    assert out.splitlines() == [
        "111\tdue=2026-05-20",
        "222\tdue=2026-05-20",
        "fines=$4.00",
    ], out

    # a member the library has never seen is just a zero state
    assert ok(db, "member", "nobody").splitlines() == ["fines=$0.00"]

    # malformed dates are rejected
    fail(db, "checkout", "333", "--member", "zed", "--date", "05/06/2026")
    fail(db, "return", "111", "--member", "max", "--date", "not-a-date")

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
    test_lending_math()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_catalog_and_holds(tmp)
        test_fines_and_blocking(tmp)
        test_store_roundtrip(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
