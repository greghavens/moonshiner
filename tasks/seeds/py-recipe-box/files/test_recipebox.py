"""Acceptance tests for the recipe box CLI. Run: python3 test_recipebox.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

PANCAKES = {
    "name": "pancakes",
    "servings": 4,
    "ingredients": [
        {"item": "flour", "qty": 500, "unit": "g"},
        {"item": "milk", "qty": 1, "unit": "cup"},
        {"item": "butter", "qty": 2, "unit": "tbsp"},
        {"item": "eggs", "qty": 2},
        {"item": "salt", "qty": 1, "unit": "tsp"},
    ],
}

SALAD = {
    "name": "salad",
    "servings": 2,
    "ingredients": [
        {"item": "lettuce", "qty": 1, "unit": ""},
        {"item": "oil", "qty": 2, "unit": "tbsp"},
        {"item": "salt", "qty": 0.5, "unit": "tsp"},
        {"item": "flour", "qty": 100, "unit": "g"},
    ],
}


def cli(db, *args):
    return subprocess.run(
        [sys.executable, "cli.py", "--db", db, *args],
        capture_output=True, text=True, env=ENV, timeout=30)


def write_recipe(tmp, recipe):
    path = os.path.join(tmp, recipe["name"] + ".json")
    with open(path, "w") as f:
        json.dump(recipe, f)
    return path


def test_units():
    import units
    assert units.to_base(2, "cup") == (96, "tsp")
    assert units.to_base(1.5, "tbsp") == (4.5, "tsp")
    assert units.to_base(4, "tsp") == (4, "tsp")
    assert units.to_base(2, "kg") == (2000, "g")
    assert units.to_base(300, "g") == (300, "g")
    assert units.to_base(3, "") == (3, "")
    try:
        units.to_base(1, "oz")
        raise AssertionError("unknown unit must raise ValueError")
    except ValueError:
        pass
    assert units.format_qty(96, "tsp") == "2 cup"
    assert units.format_qty(72, "tsp") == "1.5 cup"
    assert units.format_qty(9, "tsp") == "3 tbsp"
    assert units.format_qty(4, "tsp") == "1.33 tbsp"
    assert units.format_qty(2.5, "tsp") == "2.5 tsp"
    assert units.format_qty(2000, "g") == "2 kg"
    assert units.format_qty(1500, "g") == "1.5 kg"
    assert units.format_qty(850, "g") == "850 g"
    assert units.format_qty(3, "") == "3"
    assert units.format_qty(4.5, "") == "4.5"


def test_shopping_merge():
    import shopping
    # same dimension sums in base units; formatting picks the big unit
    out = shopping.merge([("salt", 1.5, "tsp"), ("salt", 0.5, "tsp"),
                          ("milk", 1, "cup"), ("milk", 8, "tbsp")])
    assert out == [("milk", "1.5 cup"), ("salt", "2 tsp")], out
    # incompatible dimensions for one item stay separate rows
    out = shopping.merge([("butter", 2, "tbsp"), ("butter", 200, "g"),
                          ("eggs", 2, ""), ("eggs", 1, "")])
    assert out == [("butter", "200 g"), ("butter", "2 tbsp"), ("eggs", "3")], out
    assert shopping.merge([]) == []


def test_cli(tmp):
    db = os.path.join(tmp, "box.json")
    pancakes = write_recipe(tmp, PANCAKES)
    salad = write_recipe(tmp, SALAD)

    p = cli(db, "add", pancakes)
    assert p.returncode == 0, p.stderr
    p = cli(db, "add", salad)
    assert p.returncode == 0, p.stderr

    # duplicate name rejected, db unchanged
    before = open(db).read()
    p = cli(db, "add", pancakes)
    assert p.returncode != 0
    assert open(db).read() == before

    # invalid recipes rejected and never stored
    for bad in [
        {"name": "", "servings": 2, "ingredients": [{"item": "x", "qty": 1}]},
        {"name": "soup", "servings": 0, "ingredients": [{"item": "x", "qty": 1}]},
        {"name": "soup", "servings": 2, "ingredients": [{"item": "x", "qty": -1, "unit": "g"}]},
        {"name": "soup", "servings": 2, "ingredients": [{"item": "x", "qty": 1, "unit": "oz"}]},
    ]:
        path = os.path.join(tmp, "bad.json")
        with open(path, "w") as f:
            json.dump(bad, f)
        p = cli(db, "add", path)
        assert p.returncode != 0, bad
    assert open(db).read() == before

    p = cli(db, "list")
    assert p.returncode == 0
    assert p.stdout.splitlines() == ["pancakes (serves 4)", "salad (serves 2)"]

    # scaling 4 -> 6 servings, original ingredient order, normalized units
    p = cli(db, "show", "pancakes", "--servings", "6")
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == [
        "flour\t750 g",
        "milk\t1.5 cup",
        "butter\t3 tbsp",
        "eggs\t3",
        "salt\t1.5 tsp",
    ], p.stdout

    # identity scale
    p = cli(db, "show", "salad", "--servings", "2")
    assert p.stdout.splitlines() == [
        "lettuce\t1",
        "oil\t2 tbsp",
        "salt\t0.5 tsp",
        "flour\t100 g",
    ], p.stdout

    p = cli(db, "show", "tacos", "--servings", "2")
    assert p.returncode != 0
    assert "tacos" in p.stderr
    p = cli(db, "show", "pancakes", "--servings", "0")
    assert p.returncode != 0

    # merged shopping list across recipes, pancakes scaled to 6
    p = cli(db, "shop", "pancakes=6", "salad")
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == [
        "butter\t3 tbsp",
        "eggs\t3",
        "flour\t850 g",
        "lettuce\t1",
        "milk\t1.5 cup",
        "oil\t2 tbsp",
        "salt\t2 tsp",
    ], p.stdout

    # one unknown recipe fails the whole shop command
    p = cli(db, "shop", "pancakes", "tacos")
    assert p.returncode != 0
    assert "tacos" in p.stderr


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_units()
    test_shopping_merge()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
