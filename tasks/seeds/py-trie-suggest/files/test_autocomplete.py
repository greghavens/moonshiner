"""Acceptance checks for autocomplete.py. Run: python3 test_autocomplete.py"""
from autocomplete import Autocomplete


def store():
    ac = Autocomplete()
    for term, weight in [
        ("phone case", 40),
        ("phone charger", 55),
        ("phone stand", 12),
        ("photo frame", 30),
        ("phono cable", 3),
        ("laptop sleeve", 22),
        ("laptop", 60),
    ]:
        ac.insert(term, weight)
    return ac


def test_suggest_ranked_by_weight():
    ac = store()
    assert ac.suggest("phone") == ["phone charger", "phone case",
                                   "phone stand"]
    assert ac.suggest("pho") == ["phone charger", "phone case",
                                 "photo frame", "phone stand",
                                 "phono cable"]
    assert ac.suggest("lap") == ["laptop", "laptop sleeve"]


def test_suggest_limit_and_default():
    ac = store()
    assert ac.suggest("p", limit=2) == ["phone charger", "phone case"]
    assert ac.suggest("p", limit=0) == []
    seven = ac.suggest("")
    assert len(seven) == 5, "default limit should be 5"
    assert seven[:2] == ["laptop", "phone charger"]


def test_exact_term_is_its_own_match():
    ac = store()
    assert ac.suggest("laptop") == ["laptop", "laptop sleeve"]
    assert ac.suggest("laptop sleeve") == ["laptop sleeve"]
    assert ac.suggest("laptop sleeves") == []


def test_weights_accumulate():
    ac = Autocomplete()
    ac.insert("usb hub")
    ac.insert("usb cable")
    assert ac.suggest("usb") == ["usb cable", "usb hub"]  # tie -> alphabetical
    ac.insert("usb hub", 3)
    assert ac.count("usb hub") == 4
    assert ac.suggest("usb") == ["usb hub", "usb cable"]


def test_case_and_whitespace_normalization():
    ac = Autocomplete()
    ac.insert("  MacBook Air ", 2)
    ac.insert("macbook air", 1)
    assert ac.count("MACBOOK AIR") == 3
    assert ac.suggest("MacB") == ["macbook air"]
    assert "  macbook air " in ac
    assert len(ac) == 1


def test_insert_validation():
    ac = Autocomplete()
    for term in ["", "   ", None, 42]:
        try:
            ac.insert(term)
            assert False, "inserted %r" % (term,)
        except ValueError:
            pass
    for weight in [0, -1, 2.5, "3"]:
        try:
            ac.insert("ok", weight)
            assert False, "accepted weight %r" % (weight,)
        except ValueError:
            pass
    assert len(ac) == 0


def test_delete_exact_only():
    ac = Autocomplete()
    ac.insert("car", 10)
    ac.insert("carpet", 5)
    ac.insert("cart", 1)
    assert ac.delete("car") is True
    assert "car" not in ac
    assert ac.count("car") == 0
    assert ac.suggest("car") == ["carpet", "cart"]
    assert ac.delete("car") is False          # already gone
    assert ac.delete("ca") is False           # prefix, never a term
    assert ac.delete("carpets") is False
    assert len(ac) == 2


def test_delete_then_reinsert_starts_fresh():
    ac = Autocomplete()
    ac.insert("drone", 99)
    assert ac.delete("DRONE") is True
    ac.insert("drone", 2)
    assert ac.count("drone") == 2
    assert ac.suggest("dr") == ["drone"]


def test_suggest_unknown_prefix():
    ac = store()
    assert ac.suggest("zzz") == []
    assert ac.suggest("phones") == []
    assert Autocomplete().suggest("") == []


CHECKS = [
    test_suggest_ranked_by_weight,
    test_suggest_limit_and_default,
    test_exact_term_is_its_own_match,
    test_weights_accumulate,
    test_case_and_whitespace_normalization,
    test_insert_validation,
    test_delete_exact_only,
    test_delete_then_reinsert_starts_fresh,
    test_suggest_unknown_prefix,
]


def main():
    failures = 0
    for t in CHECKS:
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
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main()
