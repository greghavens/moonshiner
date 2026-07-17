"""Acceptance tests for the Leitner SRS trainer. Run: python3 test_srs.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def srs(*args):
    return subprocess.run([sys.executable, "srs.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    deck = os.path.join(tmp, "deck.json")
    try:
        # due on a deck that doesn't exist: nothing due, file not created
        r = srs("--deck", deck, "due", "--today", "2026-07-01")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
        assert not os.path.exists(deck), "read-only commands must not create the deck"

        # add three cards on day 1 (silent success)
        for front, back in [("ohm", "unit of resistance"),
                            ("ampere", "unit of current"),
                            ("volt", "unit of potential")]:
            r = srs("--deck", deck, "add", front, back, "--today", "2026-07-01")
            assert r.returncode == 0 and r.stdout == "", (front, r.returncode, r.stdout, r.stderr)
        with open(deck) as f:
            json.load(f)  # deck file is real JSON

        # new cards are due immediately; equal box+due ties order by front
        r = srs("--deck", deck, "due", "--today", "2026-07-01")
        assert r.stdout.splitlines() == ["ampere", "ohm", "volt"], r.stdout

        # pass moves up a box, schedules interval of the NEW box
        r = srs("--deck", deck, "review", "ohm", "pass", "--today", "2026-07-01")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.strip() == "ohm: box 2, due 2026-07-03", r.stdout
        r = srs("--deck", deck, "review", "ampere", "pass", "--today", "2026-07-01")
        assert r.stdout.strip() == "ampere: box 2, due 2026-07-03", r.stdout

        # fail sends the card back to box 1, due tomorrow
        r = srs("--deck", deck, "review", "volt", "fail", "--today", "2026-07-01")
        assert r.stdout.strip() == "volt: box 1, due 2026-07-02", r.stdout

        # nothing is due the same evening
        r = srs("--deck", deck, "due", "--today", "2026-07-01")
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout)

        # next day only the failed card is back
        r = srs("--deck", deck, "due", "--today", "2026-07-02")
        assert r.stdout.splitlines() == ["volt"], r.stdout
        r = srs("--deck", deck, "review", "volt", "pass", "--today", "2026-07-02")
        assert r.stdout.strip() == "volt: box 2, due 2026-07-04", r.stdout

        # box-2 cards come due together, front A-Z on ties
        r = srs("--deck", deck, "due", "--today", "2026-07-03")
        assert r.stdout.splitlines() == ["ampere", "ohm"], r.stdout
        r = srs("--deck", deck, "review", "ohm", "pass", "--today", "2026-07-03")
        assert r.stdout.strip() == "ohm: box 3, due 2026-07-07", r.stdout

        # reviewing a card BEFORE it is due is refused and changes nothing
        with open(deck, "rb") as f:
            before = f.read()
        r = srs("--deck", deck, "review", "ohm", "pass", "--today", "2026-07-04")
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
        with open(deck, "rb") as f:
            assert f.read() == before, "refused review must not modify the deck"

        r = srs("--deck", deck, "review", "ampere", "pass", "--today", "2026-07-03")
        assert r.stdout.strip() == "ampere: box 3, due 2026-07-07", r.stdout

        r = srs("--deck", deck, "due", "--today", "2026-07-04")
        assert r.stdout.splitlines() == ["volt"], r.stdout
        r = srs("--deck", deck, "review", "volt", "fail", "--today", "2026-07-04")
        assert r.stdout.strip() == "volt: box 1, due 2026-07-05", r.stdout

        # ordering is box first, NOT alphabetical: volt (box 1) beats ampere/ohm (box 3)
        r = srs("--deck", deck, "due", "--today", "2026-07-07")
        assert r.stdout.splitlines() == ["volt", "ampere", "ohm"], r.stdout

        r = srs("--deck", deck, "stats", "--today", "2026-07-07")
        assert r.stdout.splitlines() == [
            "box 1: 1",
            "box 2: 0",
            "box 3: 2",
            "box 4: 0",
            "box 5: 0",
            "due: 3",
        ], r.stdout

        # climb to the top box; box 5 passes stay in box 5; month rollover works
        r = srs("--deck", deck, "review", "ohm", "pass", "--today", "2026-07-07")
        assert r.stdout.strip() == "ohm: box 4, due 2026-07-14", r.stdout
        r = srs("--deck", deck, "review", "ohm", "pass", "--today", "2026-07-14")
        assert r.stdout.strip() == "ohm: box 5, due 2026-07-29", r.stdout
        r = srs("--deck", deck, "review", "ohm", "pass", "--today", "2026-07-29")
        assert r.stdout.strip() == "ohm: box 5, due 2026-08-13", r.stdout

        # reviewing late is allowed and schedules from the actual review day
        r = srs("--deck", deck, "review", "volt", "pass", "--today", "2026-07-29")
        assert r.stdout.strip() == "volt: box 2, due 2026-07-31", r.stdout

        # duplicate fronts are rejected, deck untouched
        with open(deck, "rb") as f:
            before = f.read()
        r = srs("--deck", deck, "add", "ohm", "already have this", "--today", "2026-07-29")
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)
        with open(deck, "rb") as f:
            assert f.read() == before

        # unknown card
        r = srs("--deck", deck, "review", "watt", "pass", "--today", "2026-07-29")
        assert r.returncode == 1 and r.stderr.strip(), (r.returncode, r.stderr)

        # usage errors exit 2
        r = srs("--deck", deck, "review", "volt", "maybe", "--today", "2026-07-29")
        assert r.returncode == 2, (r.returncode, r.stderr)
        r = srs("--deck", deck, "due", "--today", "2026-7-4")
        assert r.returncode == 2, (r.returncode, r.stderr)
        r = srs("--deck", deck, "due")
        assert r.returncode == 2, (r.returncode, r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all srs checks passed")


if __name__ == "__main__":
    main()
