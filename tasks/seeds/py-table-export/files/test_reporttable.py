"""Acceptance checks for reporttable.py. Run: python3 test_reporttable.py"""
import json

from reporttable import Table


def make_export_table():
    t = Table(["sku", "note", "qty"])
    t.add_row({"sku": "A-1", "note": 'say "hi"', "qty": 2})
    t.add_row({"sku": "B,2", "note": "line1\nline2"})   # qty missing
    t.add_row({"sku": "Amélie", "note": None, "qty": 0})
    return t


# ---------------------------------------------------------------- existing

def test_render_exact():
    t = Table(["service", "errors", "p99 ms"])
    t.add_row({"service": "api-gateway", "errors": 3, "p99 ms": 412})
    t.add_row({"service": "auth", "errors": 0, "p99 ms": 88})
    t.add_row({"service": "billing-worker", "errors": 17})
    expected = (
        "service         errors  p99 ms\n"
        "--------------  ------  ------\n"
        "api-gateway     3       412\n"
        "auth            0       88\n"
        "billing-worker  17"
    )
    assert t.render() == expected


def test_unknown_column_rejected():
    t = Table(["a", "b"])
    try:
        t.add_row({"a": 1, "nope": 2})
        assert False, "row with unknown column accepted"
    except ValueError:
        pass
    assert t.row_count() == 0


def test_missing_values_blank():
    t = Table(["a", "b"])
    t.add_row({"b": "x"})
    assert t.rows() == [{"a": "", "b": "x"}]


def test_column_validation():
    for bad in [[], ["a", "a"], ["a", " "], ["a", ""]]:
        try:
            Table(bad)
            assert False, "accepted columns %r" % (bad,)
        except ValueError:
            pass


def test_rows_returns_copies():
    t = Table(["a"])
    t.add_row({"a": "original"})
    t.rows()[0]["a"] = "hacked"
    assert t.rows() == [{"a": "original"}]


# ------------------------------------- feature: CSV and JSON export

CSV_EXPECTED = ('sku,note,qty\r\n'
                'A-1,"say ""hi""",2\r\n'
                '"B,2","line1\nline2",\r\n'
                'Amélie,,0\r\n')

JSON_EXPECTED = ('[{"sku": "A-1", "note": "say \\"hi\\"", "qty": 2}, '
                 '{"sku": "B,2", "note": "line1\\nline2", "qty": ""}, '
                 '{"sku": "Amélie", "note": null, "qty": 0}]')


def test_to_csv_exact():
    assert make_export_table().to_csv() == CSV_EXPECTED


def test_to_csv_quoting_rules():
    t = Table(["a", "b"])
    t.add_row({"a": "plain", "b": "with space"})  # spaces never force quotes
    assert t.to_csv() == "a,b\r\nplain,with space\r\n"
    t2 = Table(["a"])
    t2.add_row({"a": "x\ry"})                     # bare CR must be quoted
    assert t2.to_csv() == 'a\r\n"x\ry"\r\n'


def test_to_json_exact_and_roundtrip():
    t = make_export_table()
    assert t.to_json() == JSON_EXPECTED
    parsed = json.loads(t.to_json())
    assert parsed[0]["qty"] == 2 and isinstance(parsed[0]["qty"], int)
    assert parsed[1]["qty"] == ""      # missing cell stays empty string
    assert parsed[2]["note"] is None   # explicit None becomes null
    assert parsed[2]["sku"] == "Amélie"


def test_to_json_preserves_column_order():
    t = make_export_table()
    orders = json.loads(t.to_json(),
                        object_pairs_hook=lambda pairs: [k for k, _ in pairs])
    assert orders == [["sku", "note", "qty"]] * 3


def test_empty_table_exports():
    t = Table(["sku", "note", "qty"])
    assert t.to_csv() == "sku,note,qty\r\n"
    assert t.to_json() == "[]"


def test_exports_do_not_mutate_table():
    t = make_export_table()
    before = t.render()
    t.to_csv()
    t.to_json()
    assert t.render() == before
    assert t.row_count() == 3


EXISTING = [
    test_render_exact,
    test_unknown_column_rejected,
    test_missing_values_blank,
    test_column_validation,
    test_rows_returns_copies,
]

FEATURE = [
    test_to_csv_exact,
    test_to_csv_quoting_rules,
    test_to_json_exact_and_roundtrip,
    test_to_json_preserves_column_order,
    test_empty_table_exports,
    test_exports_do_not_mutate_table,
]


def main():
    failures = 0
    for t in EXISTING + FEATURE:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main()
