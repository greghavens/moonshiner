"""Acceptance tests for the gradebook CLI. Run: python3 test_gradebook.py"""
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


def approx(a, b):
    return abs(a - b) < 1e-9


def test_grading():
    import grading
    # points-weighted category score
    assert approx(grading.category_percent([(8, 10), (6, 10), (10, 10)], 0), 80.0)
    # drop-lowest removes the worst RATIO, then sums points
    assert approx(grading.category_percent([(8, 10), (6, 10), (10, 10)], 1), 90.0)
    # ratio, not raw points: (8,10)=0.8 is worse than (45,50)=0.9
    assert approx(grading.category_percent([(45, 50), (8, 10), (9, 10)], 1), 90.0)
    # never drop the last survivor
    assert approx(grading.category_percent([(5, 10)], 3), 50.0)
    # missing work arrives as explicit zeros and may be dropped
    assert approx(grading.category_percent([(10, 10), (0, 10)], 1), 100.0)

    assert approx(grading.weighted_final([(80, 50), (90, 50)]), 85.0)
    # renormalization: weights need not sum to 100
    assert approx(grading.weighted_final([(80, 30), (90, 30)]), 85.0)
    assert approx(grading.weighted_final([(95, 40), (90, 60), (90, 10)]),
                  (95 * 40 + 90 * 60 + 90 * 10) / 110)

    curve = {"A": 90, "B+": 87, "B": 80, "C": 70, "D": 60}
    assert grading.letter(92.0, curve) == "A"
    assert grading.letter(90.0, curve) == "A"
    assert grading.letter(89.9, curve) == "B+"
    assert grading.letter(87.0, curve) == "B+"
    assert grading.letter(79.1, curve) == "C"
    assert grading.letter(12.0, curve) == "F"
    assert grading.letter(59.9, curve) == "F"


def setup_course(db):
    ok(db, "add-category", "homework", "--weight", "40", "--drop-lowest", "1")
    ok(db, "add-category", "exams", "--weight", "60")
    for name in ("hw1", "hw2", "hw3"):
        ok(db, "add-assignment", name, "--category", "homework", "--points", "10")
    ok(db, "add-assignment", "midterm", "--category", "exams", "--points", "100")
    ok(db, "add-assignment", "final", "--category", "exams", "--points", "100")
    rows = [
        ("alice", "hw1", "10"), ("alice", "hw2", "9"), ("alice", "hw3", "8"),
        ("bob", "hw1", "8"), ("bob", "hw2", "10"), ("bob", "hw3", "6"),
        ("carol", "hw1", "7"), ("carol", "hw3", "10"),  # carol skipped hw2
        ("alice", "midterm", "85"), ("alice", "final", "95"),
        ("bob", "midterm", "95"), ("bob", "final", "75"),
        ("carol", "midterm", "70"), ("carol", "final", "80"),
    ]
    for student, assignment, score in rows:
        ok(db, "record", student, assignment, score)


def test_cli(tmp):
    db = os.path.join(tmp, "grades.json")
    setup_course(db)

    # re-record overwrites (bob's final was actually regraded 70 -> 75)
    ok(db, "record", "bob", "final", "70")
    ok(db, "record", "bob", "final", "75")

    p = ok(db, "student", "alice")
    assert p.stdout.splitlines() == [
        "exams\t90.0%",
        "homework\t95.0%",
        "total\t92.0%\tA",
    ], p.stdout
    # default curve: 87.0 is a plain B
    p = ok(db, "student", "bob")
    assert p.stdout.splitlines() == [
        "exams\t85.0%",
        "homework\t90.0%",
        "total\t87.0%\tB",
    ], p.stdout
    # carol's missing hw2 is a hard zero, then dropped by the policy
    p = ok(db, "student", "carol")
    assert p.stdout.splitlines() == [
        "exams\t75.0%",
        "homework\t85.0%",
        "total\t79.0%\tC",
    ], p.stdout

    # install a curve with a B+ band; bob is the beneficiary
    ok(db, "curve", "A=90", "B+=87", "B=80", "C=70", "D=60")
    p = ok(db, "student", "bob")
    assert p.stdout.splitlines()[-1] == "total\t87.0%\tB+", p.stdout

    # an empty category changes nothing until it has assignments
    ok(db, "add-category", "participation", "--weight", "10")
    p = ok(db, "student", "alice")
    assert p.stdout.splitlines() == [
        "exams\t90.0%",
        "homework\t95.0%",
        "total\t92.0%\tA",
    ], p.stdout

    # ...but once it has an assignment, unscored students eat the zero
    ok(db, "add-assignment", "quiz1", "--category", "participation", "--points", "10")
    ok(db, "record", "alice", "quiz1", "9")
    p = ok(db, "student", "alice")
    assert p.stdout.splitlines() == [
        "exams\t90.0%",
        "homework\t95.0%",
        "participation\t90.0%",
        "total\t91.8%\tA",
    ], p.stdout
    p = ok(db, "student", "bob")
    assert p.stdout.splitlines() == [
        "exams\t85.0%",
        "homework\t90.0%",
        "participation\t0.0%",
        "total\t79.1%\tC",
    ], p.stdout

    # per-assignment reports
    p = ok(db, "assignment", "midterm")
    assert p.stdout.splitlines() == [
        "midterm\t3 scored\tavg 83.3%\tmin 70.0%\tmax 95.0%",
    ], p.stdout
    p = ok(db, "assignment", "quiz1")
    assert p.stdout.splitlines() == [
        "quiz1\t1 scored\tavg 90.0%\tmin 90.0%\tmax 90.0%",
        "missing\tbob,carol",
    ], p.stdout

    # error paths: bad input must not corrupt or half-apply
    before = open(db).read()
    assert cli(db, "add-category", "homework", "--weight", "10").returncode != 0
    assert cli(db, "add-category", "labs", "--weight", "-5").returncode != 0
    assert cli(db, "add-assignment", "hw1", "--category", "homework",
               "--points", "10").returncode != 0
    assert cli(db, "add-assignment", "lab1", "--category", "labs",
               "--points", "10").returncode != 0
    assert cli(db, "add-assignment", "hw9", "--category", "homework",
               "--points", "0").returncode != 0
    assert cli(db, "record", "alice", "hw9", "5").returncode != 0
    assert cli(db, "record", "alice", "hw1", "-2").returncode != 0
    assert cli(db, "curve", "A=ninety").returncode != 0
    assert cli(db, "curve", "A90").returncode != 0
    assert cli(db, "student", "mallory").returncode != 0
    assert cli(db, "assignment", "pop-quiz").returncode != 0
    assert open(db).read() == before


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_grading()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
