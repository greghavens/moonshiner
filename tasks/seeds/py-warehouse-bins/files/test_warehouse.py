"""Acceptance tests for the warehouse put-away/pick CLI. Run: python3 test_warehouse.py"""
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


def lines(out):
    return out.strip().splitlines() if out.strip() else []


def test_pick_path_math():
    import packing

    # serpentine walk: odd aisles (A, C, ...) ascending, even aisles descending
    ids = ["C-02", "A-03", "B-02", "C-10", "B-07", "A-01"]
    assert packing.pick_path(ids) == [
        "A-01", "A-03", "B-07", "B-02", "C-02", "C-10"]
    assert packing.pick_path([]) == []
    # a warehouse that only has a B aisle still walks B backwards
    assert packing.pick_path(["B-01", "B-09", "B-04"]) == ["B-09", "B-04", "B-01"]


def test_registration_errors(tmp):
    db = os.path.join(tmp, "reg.json")
    ok(db, "add-bin", "A-01", "--volume", "100", "--max-weight", "100")

    err = fail(db, "add-bin", "A-01", "--volume", "10", "--max-weight", "10")
    assert "A-01" in err
    # bin ids are AISLE-POSITION: one capital letter, dash, two digits
    for bad in ["AA-01", "A-1", "a-01", "A01", "A-001"]:
        fail(db, "add-bin", bad, "--volume", "10", "--max-weight", "10")
    fail(db, "add-bin", "A-02", "--volume", "0", "--max-weight", "10")
    fail(db, "add-bin", "A-02", "--volume", "10", "--max-weight", "-5")

    ok(db, "add-sku", "WID", "--volume", "10", "--weight", "5")
    err = fail(db, "add-sku", "WID", "--volume", "1", "--weight", "1")
    assert "WID" in err
    fail(db, "add-sku", "DUST", "--volume", "0", "--weight", "1")

    err = fail(db, "putaway", "GHOST", "--qty", "1")
    assert "GHOST" in err
    fail(db, "pick", "GHOST", "--qty", "1")
    fail(db, "find", "GHOST")
    fail(db, "putaway", "WID", "--qty", "0")
    fail(db, "pick", "WID", "--qty", "-2")

    with open(db) as fh:
        json.load(fh)  # db is real JSON


def test_putaway_rules(tmp):
    db = os.path.join(tmp, "put.json")
    ok(db, "add-bin", "A-01", "--volume", "100", "--max-weight", "100")
    ok(db, "add-bin", "A-02", "--volume", "50", "--max-weight", "500")
    ok(db, "add-bin", "B-01", "--volume", "100", "--max-weight", "1000", "--hazmat")
    ok(db, "add-bin", "B-02", "--volume", "20", "--max-weight", "100", "--hazmat")
    ok(db, "add-sku", "WID", "--volume", "10", "--weight", "5", "--min-stock", "5")
    ok(db, "add-sku", "ACID", "--volume", "10", "--weight", "10", "--hazmat",
       "--min-stock", "2")
    ok(db, "add-sku", "BRICK", "--volume", "1", "--weight", "50")

    # hazardous stock goes only to hazmat-rated bins, lowest id first
    assert lines(ok(db, "putaway", "ACID", "--qty", "3")) == ["B-01\t3"]

    # non-haz fills ordinary bins in id order, splitting when one bin can't
    # hold the lot (A-01 tops out at 10 by volume)
    assert lines(ok(db, "putaway", "WID", "--qty", "12")) == [
        "A-01\t10", "A-02\t2"]

    # consolidation first: top up the bin that already holds WID, and only
    # then fall back to an EMPTY hazmat bin as a last resort -- never one
    # holding hazardous stock
    assert lines(ok(db, "putaway", "WID", "--qty", "4")) == [
        "A-02\t3", "B-02\t1"]

    assert lines(ok(db, "putaway", "ACID", "--qty", "2")) == ["B-01\t2"]

    # not enough legal space is all-or-nothing: B-01 could take 5 more ACID
    # but B-02 holds non-haz stock, so the order must be refused whole
    before = open(db).read()
    fail(db, "putaway", "ACID", "--qty", "20")
    assert open(db).read() == before
    assert lines(ok(db, "find", "ACID")) == ["B-01\t5", "total=5"]

    # weight binds before volume here: B-02 has 10 volume free but only 95
    # weight, so exactly one 50-unit brick fits
    assert lines(ok(db, "putaway", "BRICK", "--qty", "1")) == ["B-02\t1"]
    before = open(db).read()
    fail(db, "putaway", "BRICK", "--qty", "5")
    assert open(db).read() == before

    assert lines(ok(db, "find", "WID")) == [
        "A-01\t10", "A-02\t5", "B-02\t1", "total=16"]


def test_pick_path_and_alerts(tmp):
    db = os.path.join(tmp, "pick.json")
    for bin_id in ["A-01", "A-02", "B-01", "B-02", "C-01"]:
        ok(db, "add-bin", bin_id, "--volume", "50", "--max-weight", "1000")
    ok(db, "add-sku", "GEAR", "--volume", "10", "--weight", "1",
       "--min-stock", "3")
    ok(db, "add-sku", "BOLT", "--volume", "1", "--weight", "1",
       "--min-stock", "10")

    assert lines(ok(db, "putaway", "GEAR", "--qty", "18")) == [
        "A-01\t5", "A-02\t5", "B-01\t5", "B-02\t3"]

    # picks walk the serpentine path and take greedily along it
    assert lines(ok(db, "pick", "GEAR", "--qty", "10")) == [
        "A-01\t5", "A-02\t5"]
    # aisle B is walked high-to-low: B-02 is reached before B-01
    assert lines(ok(db, "pick", "GEAR", "--qty", "6")) == [
        "B-02\t3", "B-01\t3"]

    # short stock refuses the whole pick and touches nothing
    before = open(db).read()
    err = fail(db, "pick", "GEAR", "--qty", "5")
    assert "GEAR" in err
    assert open(db).read() == before
    assert lines(ok(db, "find", "GEAR")) == ["B-01\t2", "total=2"]

    # restock alerts: on-hand below min-stock, sorted by sku
    assert lines(ok(db, "alerts")) == [
        "BOLT\thave=0\twant=10",
        "GEAR\thave=2\twant=3",
    ]
    assert lines(ok(db, "putaway", "GEAR", "--qty", "1")) == ["B-01\t1"]
    ok(db, "putaway", "BOLT", "--qty", "10")
    assert lines(ok(db, "alerts")) == []

    assert lines(ok(db, "find", "BOLT")) == ["A-01\t10", "total=10"]
    # a never-stocked sku reports an honest zero
    ok(db, "add-sku", "SHIM", "--volume", "1", "--weight", "1")
    assert lines(ok(db, "find", "SHIM")) == ["total=0"]


def test_store_roundtrip(tmp):
    import store
    path = os.path.join(tmp, "rt.json")
    # a missing db is just an empty warehouse, not a crash
    state = store.load(path)
    assert state is not None
    store.save(path, state)
    assert store.load(path) == state
    # alerts on an empty warehouse: nothing to say, clean exit
    empty_db = os.path.join(tmp, "empty.json")
    assert lines(ok(empty_db, "alerts")) == []


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_pick_path_math()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_registration_errors(tmp)
        test_putaway_rules(tmp)
        test_pick_path_and_alerts(tmp)
        test_store_roundtrip(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
