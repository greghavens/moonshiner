"""Acceptance tests for the workout log CLI. Run: python3 test_workout.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def workout(log, *args):
    return subprocess.run([sys.executable, "workout.py", "--log", log, *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=ENV, timeout=30)


def add_ok(log, date, exercise, *sets):
    args = ["add", date, exercise]
    for s in sets:
        args += ["--set", s]
    r = workout(log, *args)
    assert r.returncode == 0 and r.stdout == "", (date, exercise, sets, r.returncode, r.stderr)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    try:
        # ---------- add: validation and persistence ----------
        log = os.path.join(tmp, "lift.json")

        add_ok(log, "2026-03-02", "squat", "5x100", "5x100", "5x100")
        with open(log, encoding="utf-8") as f:
            json.load(f)  # the log is real JSON

        # bad inputs are refused with exit 2 and a complaint on stderr
        for args in [
            ("add", "2026-3-02", "squat", "--set", "5x100"),     # sloppy date
            ("add", "2026-02-30", "squat", "--set", "5x100"),    # impossible date
            ("add", "2026-03-02", "squat", "--set", "5x"),       # missing weight
            ("add", "2026-03-02", "squat", "--set", "x100"),     # missing reps
            ("add", "2026-03-02", "squat", "--set", "0x100"),    # zero reps
            ("add", "2026-03-02", "squat", "--set", "5x-40"),    # negative weight
            ("add", "2026-03-02", "squat", "--set", "5"),        # not a set at all
            ("add", "2026-03-02", "squat"),                      # no sets given
        ]:
            r = workout(log, *args)
            assert r.returncode == 2, (args, r.returncode, r.stdout, r.stderr)
            assert r.stderr.strip() != "", args
        # and none of that garbage was recorded
        r = workout(log, "report", "2026-W10")
        assert r.stdout == "squat: 1500 kg (3 sets)\n", r.stdout

        # ---------- pr: personal records ----------
        add_ok(log, "2026-03-04", "squat", "5x102.5", "5x102.5", "4x102.5")
        add_ok(log, "2026-03-06", "squat", "3x105", "5x95")

        r = workout(log, "pr", "squat")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout == ("best weight: 105 kg x 3 (2026-03-06)\n"
                            "best e1rm: 119.6 kg (2026-03-04)\n"), r.stdout

        # exercise names are case-insensitive
        r = workout(log, "pr", "SQUAT")
        assert "best weight: 105 kg x 3 (2026-03-06)" in r.stdout, r.stdout

        # best-weight ties: more reps wins, then the earlier date
        prlog = os.path.join(tmp, "pr.json")
        add_ok(prlog, "2026-03-02", "curl", "8x20")
        add_ok(prlog, "2026-03-04", "curl", "10x20")
        add_ok(prlog, "2026-03-06", "curl", "10x20")
        r = workout(prlog, "pr", "curl")
        assert r.stdout == ("best weight: 20 kg x 10 (2026-03-04)\n"
                            "best e1rm: 26.7 kg (2026-03-04)\n"), r.stdout

        # unknown exercise: nothing on stdout, exit 1
        r = workout(log, "pr", "yoga")
        assert r.returncode == 1 and r.stdout == "", (r.returncode, r.stdout)

        # ---------- suggest: progression rules ----------
        # every set of the latest session hit 5+ reps -> add 2.5 kg
        blog = os.path.join(tmp, "bench.json")
        add_ok(blog, "2026-03-03", "bench", "5x60", "5x60")
        add_ok(blog, "2026-03-05", "bench", "6x60", "5x60")
        r = workout(blog, "suggest", "bench")
        assert r.returncode == 0 and r.stdout == "bench: increase to 62.5 kg\n", (r.returncode, r.stdout)

        # a short rep in the latest session -> hold
        r = workout(log, "suggest", "squat")
        assert r.stdout == "squat: hold at 105 kg\n", r.stdout

        # top weight frozen for the last three sessions -> plateau, deload
        dlog = os.path.join(tmp, "dead.json")
        add_ok(dlog, "2026-03-02", "deadlift", "5x140")
        add_ok(dlog, "2026-03-04", "deadlift", "4x140", "5x100")
        add_ok(dlog, "2026-03-06", "deadlift", "3x140")
        r = workout(dlog, "suggest", "deadlift")
        assert r.stdout == "deadlift: deload to 125 kg\n", r.stdout

        # plateau wins even when the reps would justify an increase
        olog = os.path.join(tmp, "ohp.json")
        add_ok(olog, "2026-03-02", "ohp", "5x40")
        add_ok(olog, "2026-03-04", "ohp", "5x40")
        add_ok(olog, "2026-03-06", "ohp", "5x40")
        r = workout(olog, "suggest", "ohp")
        assert r.stdout == "ohp: deload to 35 kg\n", r.stdout

        # two identical tops are not yet a plateau (that takes three)
        r = workout(blog, "suggest", "bench")
        assert r.stdout == "bench: increase to 62.5 kg\n", r.stdout

        # no sessions at all: exit 1, silent
        r = workout(blog, "suggest", "squat")
        assert r.returncode == 1 and r.stdout == "", (r.returncode, r.stdout)

        # ---------- report: weekly volume ----------
        rlog = os.path.join(tmp, "week.json")
        add_ok(rlog, "2026-03-02", "squat", "5x100", "5x100")
        add_ok(rlog, "2026-03-04", "Bench", "5x60")
        add_ok(rlog, "2026-03-04", "curl", "7x22.5")
        add_ok(rlog, "2026-03-07", "squat", "3x105")
        add_ok(rlog, "2026-03-09", "squat", "5x110")  # ISO week 11

        r = workout(rlog, "report", "2026-W10")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout == ("bench: 300 kg (1 set)\n"
                            "curl: 157.5 kg (1 set)\n"
                            "squat: 1315 kg (3 sets)\n"), r.stdout

        r = workout(rlog, "report", "2026-W11")
        assert r.stdout == "squat: 550 kg (1 set)\n", r.stdout

        # an empty week: silent, exit 1
        r = workout(rlog, "report", "2026-W20")
        assert r.returncode == 1 and r.stdout == "", (r.returncode, r.stdout)

        # week format is strict
        for bad in ("2026-10", "W10", "2026-W", "2026-W99", "soon"):
            r = workout(rlog, "report", bad)
            assert r.returncode == 2 and r.stderr.strip() != "", (bad, r.returncode)

        # ---------- reading commands on a missing or broken log ----------
        ghost = os.path.join(tmp, "ghost.json")
        assert workout(ghost, "pr", "squat").returncode == 1
        assert workout(ghost, "suggest", "squat").returncode == 1
        assert workout(ghost, "report", "2026-W10").returncode == 1
        assert not os.path.exists(ghost), "read commands must not create the log"

        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("{not json at all")
        r = workout(broken, "pr", "squat")
        assert r.returncode == 2 and r.stderr.strip() != "", (r.returncode, r.stderr)

        print("ok")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
