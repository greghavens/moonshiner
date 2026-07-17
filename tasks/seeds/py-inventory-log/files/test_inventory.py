"""Acceptance tests for the inventory manager. Run: python3 test_inventory.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def inv(log, *args):
    return subprocess.run([sys.executable, "inventory.py", "--log", log, *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def main():
    tmp = tempfile.mkdtemp(dir=".")
    log = os.path.join(tmp, "stock.log")
    state = log + ".state"
    try:
        # fresh start: stock is empty, nothing gets created by a read
        r = inv(log, "stock")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
        assert not os.path.exists(log) and not os.path.exists(state)

        # stock in/out, silent success
        assert inv(log, "in", "WID-1", "5").returncode == 0
        assert os.path.exists(log) and os.path.exists(state), "in must create log and snapshot"
        assert inv(log, "in", "BOLT-9", "100").returncode == 0
        r = inv(log, "out", "WID-1", "2")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)

        # derived views
        r = inv(log, "stock")
        assert r.stdout.splitlines() == ["BOLT-9: 100", "WID-1: 3"], r.stdout
        r = inv(log, "stock", "WID-1")
        assert r.stdout.strip() == "3", r.stdout
        r = inv(log, "stock", "NEVER-SEEN")
        assert r.returncode == 0 and r.stdout.strip() == "0", (r.returncode, r.stdout)

        # the log is one JSON object per line with exactly op/sku/qty
        lines = read_bytes(log).decode().splitlines()
        assert len(lines) == 3, lines
        for line in lines:
            ev = json.loads(line)
            assert set(ev) == {"op", "sku", "qty"}, ev
            assert ev["op"] in ("in", "out") and isinstance(ev["qty"], int), ev
        assert json.loads(lines[0]) == {"op": "in", "sku": "WID-1", "qty": 5}, lines[0]

        # overdraw is refused and writes nothing
        before_log, before_state = read_bytes(log), read_bytes(state)
        r = inv(log, "out", "WID-1", "99")
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
        assert read_bytes(log) == before_log, "refused out must not touch the log"
        assert read_bytes(state) == before_state, "refused out must not touch the snapshot"
        assert inv(log, "stock", "WID-1").stdout.strip() == "3"

        # bad quantities are usage errors and write nothing
        for bad in ["0", "-3", "2.5", "many"]:
            r = inv(log, "in", "WID-1", bad)
            assert r.returncode == 2, (bad, r.returncode, r.stderr)
        assert read_bytes(log) == before_log

        # the log is append-only: old bytes stay put
        assert inv(log, "in", "WID-1", "1").returncode == 0
        after = read_bytes(log)
        assert after.startswith(before_log), "log must be append-only"
        assert len(after.decode().splitlines()) == 4

        # draw down to zero: zero-stock SKUs drop out of the listing
        assert inv(log, "out", "BOLT-9", "100").returncode == 0
        r = inv(log, "stock")
        assert r.stdout.splitlines() == ["WID-1: 4"], r.stdout
        r = inv(log, "stock", "BOLT-9")
        assert r.stdout.strip() == "0", r.stdout

        # history reads the log in order
        r = inv(log, "history", "WID-1")
        assert r.stdout.splitlines() == ["in 5", "out 2", "in 1"], r.stdout
        r = inv(log, "history", "NEVER-SEEN")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)

        # ---- snapshot corruption story
        os.remove(state)
        r = inv(log, "stock")
        assert r.returncode == 1 and "rebuild" in r.stderr.lower(), (r.returncode, r.stderr)
        before_log = read_bytes(log)
        r = inv(log, "in", "WID-1", "1")
        assert r.returncode == 1 and "rebuild" in r.stderr.lower(), (r.returncode, r.stderr)
        assert read_bytes(log) == before_log, "no snapshot -> no writes, not even to the log"
        r = inv(log, "out", "WID-1", "1")
        assert r.returncode == 1, (r.returncode, r.stderr)
        assert read_bytes(log) == before_log

        # history still works straight off the log
        r = inv(log, "history", "BOLT-9")
        assert r.returncode == 0 and r.stdout.splitlines() == ["in 100", "out 100"], r.stdout

        # rebuild folds the log and restores service
        r = inv(log, "rebuild")
        assert r.returncode == 0 and r.stdout.strip() == "rebuilt 5 events", (r.returncode, r.stdout)
        r = inv(log, "stock")
        assert r.stdout.splitlines() == ["WID-1: 4"], r.stdout

        # another tool appends directly: snapshot is stale by design until rebuild
        with open(log, "a") as f:
            f.write('{"op": "in", "sku": "WID-1", "qty": 6}\n')
        r = inv(log, "stock", "WID-1")
        assert r.stdout.strip() == "4", ("stock must serve the snapshot, not refold", r.stdout)
        r = inv(log, "rebuild")
        assert r.stdout.strip() == "rebuilt 6 events", r.stdout
        r = inv(log, "stock", "WID-1")
        assert r.stdout.strip() == "10", r.stdout

        # unknown subcommand is a usage error
        r = inv(log, "shrink", "WID-1", "1")
        assert r.returncode == 2, (r.returncode, r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all inventory checks passed")


if __name__ == "__main__":
    main()
