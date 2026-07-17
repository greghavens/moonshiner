"""Acceptance checks for notes.py. Run: python3 test_notes.py"""
from notes import NoteStore


def make_store():
    s = NoteStore()
    s.add("Standup notes", ["work", "meeting"])       # id 1
    s.add("Groceries", ["home", "errand"])            # id 2
    s.add("Quarterly plan", ["work", "planning", "urgent"])  # id 3
    s.add("Boiler service", ["home", "urgent"])       # id 4
    s.add("Reading list", ["leisure"])                # id 5
    s.add("Deploy checklist", ["work", "urgent", "archived"])  # id 6
    return s


def ids(notes):
    return [n.id for n in notes]


# ---------------------------------------------------------------- existing

def test_add_get_and_ids_increment():
    s = NoteStore()
    a = s.add("  First note  ", ["Alpha"])
    b = s.add("Second", ["beta"])
    assert (a, b) == (1, 2)
    assert s.get(1).title == "First note"
    assert s.get(2).tags == frozenset({"beta"})
    assert len(s) == 2


def test_tags_are_normalized():
    s = NoteStore()
    s.add("Meeting prep", ["Work", " MEETING ", "q3_planning"])
    assert s.get(1).tags == frozenset({"work", "meeting", "q3_planning"})
    try:
        s.add("Bad", ["no spaces"])
        assert False, "tag with a space accepted"
    except ValueError:
        pass
    try:
        s.add("Bad", [""])
        assert False, "empty tag accepted"
    except ValueError:
        pass
    try:
        s.add("   ", ["ok"])
        assert False, "blank title accepted"
    except ValueError:
        pass


def test_find_any_ordered_by_id():
    s = make_store()
    assert ids(s.find_any("home", "leisure")) == [2, 4, 5]
    assert ids(s.find_any("WORK")) == [1, 3, 6]
    assert ids(s.find_any("nosuch")) == []


def test_find_all_requires_every_tag():
    s = make_store()
    assert ids(s.find_all("work", "urgent")) == [3, 6]
    assert ids(s.find_all("home", "errand")) == [2]
    assert ids(s.find_all("work", "leisure")) == []


def test_remove_and_all_tags():
    s = make_store()
    s.remove(2)
    assert len(s) == 5
    try:
        s.get(2)
        assert False, "removed note still retrievable"
    except KeyError:
        pass
    assert s.all_tags() == ["archived", "home", "leisure", "meeting",
                            "planning", "urgent", "work"]


# ------------------------------------------- feature: boolean search()

def test_search_and():
    s = make_store()
    assert ids(s.search("work AND urgent")) == [3, 6]


def test_search_or_and_precedence():
    s = make_store()
    # AND binds tighter than OR: home OR (work AND urgent)
    assert ids(s.search("home OR work AND urgent")) == [2, 3, 4, 6]
    assert ids(s.search("meeting OR errand")) == [1, 2]


def test_search_not_binds_tightest():
    s = make_store()
    # (NOT home) AND urgent
    assert ids(s.search("NOT home AND urgent")) == [3, 6]
    assert ids(s.search("NOT phone")) == [1, 2, 3, 4, 5, 6]
    assert ids(s.search("NOT NOT urgent")) == [3, 4, 6]


def test_search_parentheses():
    s = make_store()
    assert ids(s.search("(home OR work) AND NOT archived")) == [1, 2, 3, 4]
    assert ids(s.search("urgent AND (home OR archived)")) == [4, 6]


def test_search_is_case_insensitive():
    s = make_store()
    assert ids(s.search("Work and URGENT")) == [3, 6]
    assert ids(s.search("not home AND urgent")) == [3, 6]


def test_search_no_matches_is_empty_list():
    s = make_store()
    assert s.search("phone") == []
    assert ids(s.search("work AND leisure")) == []


def test_search_rejects_malformed_queries():
    s = make_store()
    bad = ["", "   ", "work AND", "AND work", "work urgent",
           "(work OR home", "work)", "work & home", "NOT",
           "work OR (home AND)", "work AND OR home"]
    for q in bad:
        try:
            s.search(q)
            assert False, "accepted malformed query: %r" % q
        except ValueError:
            pass


EXISTING = [
    test_add_get_and_ids_increment,
    test_tags_are_normalized,
    test_find_any_ordered_by_id,
    test_find_all_requires_every_tag,
    test_remove_and_all_tags,
]

FEATURE = [
    test_search_and,
    test_search_or_and_precedence,
    test_search_not_binds_tightest,
    test_search_parentheses,
    test_search_is_case_insensitive,
    test_search_no_matches_is_empty_list,
    test_search_rejects_malformed_queries,
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
