"""Acceptance tests for the pantry toolkit. Run: python3 test_pantry.py"""
import subprocess
import sys


def test_intake_normalization():
    from pantry.intake import record_donation

    rec = record_donation("  Rosa  DELGADO ", " Tomato   Soup ", "12 CANS", 3, "canned goods")
    assert rec.donor == "rosa delgado", rec.donor
    assert rec.item == "tomato soup", rec.item
    assert rec.qty == 12.0 and rec.unit == "can", (rec.qty, rec.unit)
    assert rec.day == 3 and rec.category == "canned goods"

    rec2 = record_donation("Ed", "rice", "3.5 kgs", 9, "dry goods")
    assert rec2.qty == 3.5 and rec2.unit == "kg", (rec2.qty, rec2.unit)

    for bad in ("kg 3", "3", "0 kg", "-2 can", "three cans"):
        try:
            record_donation("Ed", "rice", bad, 1, "dry goods")
        except ValueError:
            pass
        else:
            raise AssertionError(f"quantity {bad!r} should have been rejected")


def _season():
    from pantry.intake import record_donation

    return [
        record_donation("Rosa Delgado", "tomato soup", "12 cans", 1, "canned goods"),
        record_donation("Ed Park", "rice", "5 kg", 2, "dry goods"),
        record_donation("Rosa Delgado", "black beans", "6 cans", 8, "canned goods"),
        record_donation("Mia Chen", "flour", "5 kg", 8, "dry goods"),
        record_donation("Ed Park", "lentils", "2 kg", 9, "dry goods"),
    ]


def test_weekly_summary_and_digest():
    from pantry.intake import intake_digest
    from pantry.reports import weekly_summary

    records = _season()
    expected = {
        (1, "canned goods", "can"): 12.0,
        (1, "dry goods", "kg"): 5.0,
        (2, "canned goods", "can"): 6.0,
        (2, "dry goods", "kg"): 7.0,
    }
    assert weekly_summary(records) == expected, weekly_summary(records)
    assert intake_digest(records) == expected
    assert weekly_summary([]) == {}

    try:
        weekly_summary([("not", "a", "record")])
    except TypeError:
        pass
    else:
        raise AssertionError("weekly_summary must reject non-records")


def test_top_donors():
    from pantry.reports import top_donors

    ranked = top_donors(_season())
    assert ranked == [
        ("rosa delgado", 18.0),
        ("ed park", 7.0),
        ("mia chen", 5.0),
    ], ranked
    assert top_donors(_season(), n=1) == [("rosa delgado", 18.0)]


def test_bin_index():
    from pantry.storage import BinIndex

    idx = BinIndex([("A1", 10.0), ("B1", 4.0)])
    assert idx.place("canned goods", 7) == "A1"
    assert idx.place("produce", 3) == "A1"  # exactly fills A1
    assert idx.place("produce", 4) == "B1"
    assert idx.load_of("A1") == 10.0 and idx.load_of("B1") == 4.0
    try:
        idx.place("dry goods", 0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("full shelves must refuse new stock")
    assert idx.manifest() == [
        ("A1", 10.0, ["canned goods", "produce"]),
        ("B1", 4.0, ["produce"]),
    ], idx.manifest()


def test_shelving_records():
    from pantry.intake import shelve_records

    idx = shelve_records(_season(), [("S1", 20.0), ("S2", 20.0)])
    # first-fit: 12 and 5 land in S1, 6 and 5 overflow to S2, the final
    # 2 kg still fits back in S1 (17 + 2 <= 20)
    assert idx.load_of("S1") == 19.0, idx.load_of("S1")
    assert idx.load_of("S2") == 11.0, idx.load_of("S2")


def test_export_script():
    with open("intake_week.csv", "w") as fh:
        fh.write("# week 28 drop-offs\n")
        fh.write("canned goods,12\n")
        fh.write("produce,7\n")
        fh.write("dry goods,15\n")
        fh.write("produce,4\n")
    proc = subprocess.run(
        [sys.executable, "export_shelf.py", "intake_week.csv"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "A1: 19 kg — canned goods, produce",
        "A2: 19 kg — dry goods, produce",
        "B1: 0 kg — empty",
    ], proc.stdout

    usage = subprocess.run([sys.executable, "export_shelf.py"], capture_output=True, text=True)
    assert usage.returncode == 2, usage.returncode
    assert "usage:" in usage.stderr


def main():
    test_intake_normalization()
    test_weekly_summary_and_digest()
    test_top_donors()
    test_bin_index()
    test_shelving_records()
    test_export_script()
    print("all pantry tests passed")


if __name__ == "__main__":
    main()
