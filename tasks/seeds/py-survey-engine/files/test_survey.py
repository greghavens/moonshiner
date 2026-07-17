"""Acceptance tests for the survey engine CLI. Run: python3 test_survey.py"""
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

CAFETERIA = {
    "title": "Cafeteria pulse",
    "questions": [
        {"id": "diet", "text": "Dietary preference?", "type": "choice",
         "options": ["omnivore", "vegetarian", "vegan"]},
        {"id": "meat_rating", "text": "Rate the meat options", "type": "number",
         "min": 1, "max": 5, "show_if": {"diet": "omnivore"}},
        {"id": "veg_ok", "text": "Enough vegan mains?", "type": "choice",
         "options": ["yes", "no"], "show_if": {"diet": "vegan"}},
        {"id": "why", "text": "Tell us more", "type": "text",
         "required": False, "show_if": {"veg_ok": "no"}},
        {"id": "recommend", "text": "Recommend the cafeteria?", "type": "choice",
         "options": ["yes", "no"]},
    ],
}


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


def write_defn(tmp, name, defn):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(defn, fh)
    return path


def test_branching_logic():
    import survey

    # nothing answered: only unconditional questions are on screen
    assert survey.visible_questions(CAFETERIA, {}) == ["diet", "recommend"]
    # a satisfied show-if reveals the dependent question, in definition order
    assert survey.visible_questions(CAFETERIA, {"diet": "vegan"}) == [
        "diet", "veg_ok", "recommend"]
    assert survey.visible_questions(CAFETERIA, {"diet": "omnivore"}) == [
        "diet", "meat_rating", "recommend"]
    # branching chains: `why` needs veg_ok=no, and veg_ok itself needs diet=vegan
    assert survey.visible_questions(
        CAFETERIA, {"diet": "vegan", "veg_ok": "no"}) == [
        "diet", "veg_ok", "why", "recommend"]
    assert survey.visible_questions(
        CAFETERIA, {"diet": "omnivore", "veg_ok": "no"}) == [
        "diet", "meat_rating", "recommend"]

    # a complete, consistent response validates clean
    assert survey.validate(CAFETERIA, {
        "diet": "omnivore", "meat_rating": "4", "recommend": "yes"}) == []
    # optional text may be skipped even when visible
    assert survey.validate(CAFETERIA, {
        "diet": "vegan", "veg_ok": "no", "recommend": "no"}) == []

    # every complaint names the offending question
    problems = survey.validate(CAFETERIA, {"diet": "omnivore", "recommend": "yes"})
    assert problems and any("meat_rating" in p for p in problems), problems
    problems = survey.validate(
        CAFETERIA, {"diet": "vegetarian", "veg_ok": "yes", "recommend": "no"})
    assert problems and any("veg_ok" in p for p in problems), problems
    problems = survey.validate(CAFETERIA, {
        "diet": "omnivore", "meat_rating": "twelve", "recommend": "yes"})
    assert problems and any("meat_rating" in p for p in problems), problems


def test_define_and_validation(tmp):
    db = os.path.join(tmp, "pulse.json")
    defn = write_defn(tmp, "cafeteria.json", CAFETERIA)

    # nothing works before define
    fail(db, "tally")
    fail(db, "submit", "--id", "r0", "diet=vegan")
    assert not os.path.exists(db)

    ok(db, "define", defn)
    before = open(db).read()
    fail(db, "define", defn)  # no silent re-define over collected data
    assert open(db).read() == before

    # broken definitions are refused up front (fresh db path each time)
    bad_cases = [
        {"questions": [{"id": "a", "type": "choice", "options": ["x"]},
                       {"id": "a", "type": "choice", "options": ["y"]}]},
        {"questions": [{"id": "a", "type": "choice", "options": ["x"],
                        "show_if": {"ghost": "x"}}]},
        {"questions": [{"id": "a", "type": "choice", "options": ["x"],
                        "show_if": {"b": "x"}},
                       {"id": "b", "type": "choice", "options": ["x"]}]},
        {"questions": [{"id": "a", "type": "choice"}]},
        {"questions": [{"id": "a", "type": "slider"}]},
    ]
    for i, bad in enumerate(bad_cases):
        bad_db = os.path.join(tmp, f"bad{i}.json")
        fail(bad_db, "define", write_defn(tmp, f"bad{i}-defn.json", bad))
        assert not os.path.exists(bad_db), (i, "db must not be created")

    # rejected submissions never touch the db
    before = open(db).read()
    err = fail(db, "submit", "--id", "r1", "diet=omnivore", "recommend=yes")
    assert "meat_rating" in err  # visible+required, missing
    err = fail(db, "submit", "--id", "r1", "diet=vegetarian",
               "veg_ok=yes", "recommend=no")
    assert "veg_ok" in err  # answered while hidden
    fail(db, "submit", "--id", "r1", "diet=vegan", "veg_ok=yes",
         "why=nice", "recommend=yes")  # chained-hidden `why`
    fail(db, "submit", "--id", "r1", "diet=carnivore", "recommend=yes")
    fail(db, "submit", "--id", "r1", "diet=omnivore", "meat_rating=9",
         "recommend=yes")
    fail(db, "submit", "--id", "r1", "diet=omnivore", "meat_rating=4.5",
         "recommend=yes")
    err = fail(db, "submit", "--id", "r1", "diet=vegan", "veg_ok=yes",
               "recommend=yes", "extra=1")
    assert "extra" in err
    fail(db, "submit", "--id", "r1", "diet")  # not a key=value pair
    assert open(db).read() == before

    ok(db, "submit", "--id", "r1", "diet=omnivore", "meat_rating=4",
       "recommend=yes")
    err = fail(db, "submit", "--id", "r1", "diet=vegan", "veg_ok=yes",
               "recommend=yes")
    assert "r1" in err  # duplicate respondent

    with open(db) as fh:
        json.load(fh)  # db is real JSON


def test_tally_and_export(tmp):
    db = os.path.join(tmp, "tally.json")
    ok(db, "define", write_defn(tmp, "defn.json", CAFETERIA))

    # tally/export on an empty survey: zero rows, header still exported
    assert ok(db, "tally").splitlines() == [
        "diet\tomnivore\t0\t0.0%",
        "diet\tvegetarian\t0\t0.0%",
        "diet\tvegan\t0\t0.0%",
        "veg_ok\tyes\t0\t0.0%",
        "veg_ok\tno\t0\t0.0%",
        "recommend\tyes\t0\t0.0%",
        "recommend\tno\t0\t0.0%",
    ]
    assert ok(db, "export").strip().splitlines() == [
        "respondent,diet,meat_rating,veg_ok,why,recommend"]

    ok(db, "submit", "--id", "r1", "diet=omnivore", "meat_rating=4",
       "recommend=yes")
    ok(db, "submit", "--id", "r2", "diet=vegan", "veg_ok=no",
       'why=too salty, honestly "meh"', "recommend=no")
    ok(db, "submit", "--id", "r3", "diet=vegan", "veg_ok=yes", "recommend=yes")
    ok(db, "submit", "--id", "r4", "diet=vegetarian", "recommend=yes")
    ok(db, "submit", "--id", "r5", "diet=vegan", "veg_ok=no", "recommend=no")

    # percentages are of the respondents who were SHOWN the question:
    # veg_ok was only ever shown to the three vegans
    assert ok(db, "tally").splitlines() == [
        "diet\tomnivore\t1\t20.0%",
        "diet\tvegetarian\t1\t20.0%",
        "diet\tvegan\t3\t60.0%",
        "veg_ok\tyes\t1\t33.3%",
        "veg_ok\tno\t2\t66.7%",
        "recommend\tyes\t3\t60.0%",
        "recommend\tno\t2\t40.0%",
    ]

    out = ok(db, "export")
    rows = list(csv.reader(io.StringIO(out)))
    assert rows == [
        ["respondent", "diet", "meat_rating", "veg_ok", "why", "recommend"],
        ["r1", "omnivore", "4", "", "", "yes"],
        ["r2", "vegan", "", "no", 'too salty, honestly "meh"', "no"],
        ["r3", "vegan", "", "yes", "", "yes"],
        ["r4", "vegetarian", "", "", "", "yes"],
        ["r5", "vegan", "", "no", "", "no"],
    ], rows
    # commas and quotes survive via real CSV quoting, not luck
    assert 'too salty, honestly ""meh""' in out


def test_never_shown_question(tmp):
    db = os.path.join(tmp, "dead.json")
    defn = {"questions": [
        {"id": "a", "type": "choice", "options": ["x", "y"]},
        {"id": "b", "type": "choice", "options": ["p", "q"],
         "show_if": {"a": "y"}},
    ]}
    ok(db, "define", write_defn(tmp, "dead-defn.json", defn))
    ok(db, "submit", "--id", "r1", "a=x")
    # a question nobody ever saw tallies to zero, not a crash
    assert ok(db, "tally").splitlines() == [
        "a\tx\t1\t100.0%",
        "a\ty\t0\t0.0%",
        "b\tp\t0\t0.0%",
        "b\tq\t0\t0.0%",
    ]


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_branching_logic()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_define_and_validation(tmp)
        test_tally_and_export(tmp)
        test_never_shown_question(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
