"""Gate + behavior tests. CI runs exactly: python3 -W error test_tagscan.py"""
import compileall


def test_sources_compile_clean():
    # The runner escalates every warning to an error (-W error). A fresh
    # force-recompile of the whole tree must succeed — stale __pycache__
    # entries don't count.
    ok = compileall.compile_dir(".", quiet=1, force=True)
    assert ok, "compileall failed — the tree does not compile warning-clean"


LINES = [
    "07:58 boot scanner-4 ok",
    "09:14 checkout IT-0042 room 12b",
    "09:20 checkout HR-1200 room 2",
    "09:31 checkin IT-0042 room 12b",
    "10:02 checkout FA-0007 room annex",
    "10:15 checkout IT-9999 hallway",
    "10:40 checkin HR-1200 room 2",
]


def test_parse_line():
    from scanlog import parse_line

    assert parse_line(LINES[1]) == {
        "time": "09:14",
        "kind": "checkout",
        "tag": "IT-0042",
        "room": "12b",
    }
    assert parse_line(LINES[3])["kind"] == "checkin"
    assert parse_line("07:58 boot scanner-4 ok") is None  # not a scan event
    assert parse_line("10:15 checkout IT-9999 hallway") is None  # no room sticker
    assert parse_line("09:14 checkout it-0042 room 3") is None  # bad tag case
    assert parse_line("09:14 checkout IT-42 room 3") is None  # short number
    assert parse_line("") is None
    # Event words arrive from the wire, built at runtime — classification
    # must be by value, not by object identity.
    wire = " ".join(["11:05", "".join(["check", "out"]), "IT-0042", "room 9"])
    assert parse_line(wire) == {
        "time": "11:05",
        "kind": "checkout",
        "tag": "IT-0042",
        "room": "9",
    }


def test_outstanding():
    from scanlog import outstanding

    assert outstanding(LINES) == ["FA-0007"]
    assert outstanding([]) == []
    assert outstanding(LINES[:3]) == ["HR-1200", "IT-0042"]


def test_tag_rules():
    from tagrules import is_tag

    assert is_tag("IT-0042")
    assert is_tag("HR-1200")
    assert not is_tag("ITX-0042")
    assert not is_tag("IT-004")
    assert not is_tag("IT_0042")
    assert not is_tag("prefix IT-0042")


def test_note_dates():
    from scanlog import note_dates

    note = "audited 2026.07.01, re-checked 2026.07.02 (sticker 12x34)"
    assert note_dates(note) == ["2026-07-01", "2026-07-02"]
    assert note_dates("no stamps here, honest") == []
    # the dots must be literal dots, not any-character wildcards
    assert note_dates("build 2026x07y01 is not a date stamp") == []


def main():
    test_sources_compile_clean()
    test_parse_line()
    test_outstanding()
    test_tag_rules()
    test_note_dates()
    print("all tagscan tests passed")


if __name__ == "__main__":
    main()
