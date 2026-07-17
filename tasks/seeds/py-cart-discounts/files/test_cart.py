"""Acceptance checks for cart.py. Run: python3 test_cart.py"""
from cart import Cart, format_cents


def make_cart():
    c = Cart()
    c.add_item("KB1", "Mech keyboard", 7500)
    c.add_item("USB", "USB-C cable", 1250, qty=2)
    return c  # subtotal 10000


# ---------------------------------------------------------------- existing

def test_add_accumulates_same_sku():
    c = make_cart()
    c.add_item("USB", "USB-C cable", 1250)
    assert c.item_count() == 4
    assert c.subtotal_cents() == 11250
    assert c.lines() == [("KB1", "Mech keyboard", 7500, 1),
                         ("USB", "USB-C cable", 1250, 3)]


def test_re_add_with_different_details_rejected():
    c = make_cart()
    try:
        c.add_item("USB", "USB-C cable", 999)
        assert False, "price change on re-add accepted"
    except ValueError:
        pass


def test_remove_item():
    c = make_cart()
    c.remove_item("KB1")
    assert c.subtotal_cents() == 2500
    assert [ln[0] for ln in c.lines()] == ["USB"]
    try:
        c.remove_item("NOPE")
        assert False, "removing unknown sku did not raise"
    except KeyError:
        pass


def test_total_matches_subtotal_and_receipt():
    c = make_cart()
    assert c.total_cents() == c.subtotal_cents() == 10000
    assert Cart().subtotal_cents() == 0
    assert c.receipt().splitlines()[-1] == "TOTAL $100.00"


def test_format_cents():
    assert format_cents(0) == "$0.00"
    assert format_cents(7) == "$0.07"
    assert format_cents(1234) == "$12.34"
    try:
        format_cents(-1)
        assert False, "negative cents formatted"
    except ValueError:
        pass


def test_item_validation():
    c = Cart()
    for args in [("A", "a", -1), ("A", "a", 12.5), ("A", "a", 100, 0),
                 ("A", "a", 100, 1.5)]:
        try:
            c.add_item(*args)
            assert False, "accepted bad item %r" % (args,)
        except ValueError:
            pass


# ------------------------------------ feature: stacking discount rules

def test_discounts_stack_in_priority_order():
    c = make_cart()
    c.add_discount("SAVE10", "percent", 10, priority=1)
    c.add_discount("FIVE", "fixed", 500, priority=2)
    assert c.applied_discounts() == [("SAVE10", 1000), ("FIVE", 500)]
    assert c.total_cents() == 8500

    c2 = make_cart()  # same rules, opposite priorities -> different total
    c2.add_discount("SAVE10", "percent", 10, priority=2)
    c2.add_discount("FIVE", "fixed", 500, priority=1)
    assert c2.applied_discounts() == [("FIVE", 500), ("SAVE10", 950)]
    assert c2.total_cents() == 8550


def test_priority_ties_break_by_code():
    c = make_cart()
    c.add_discount("beta", "percent", 10, priority=5)
    c.add_discount("alfa", "percent", 10, priority=5)
    assert c.applied_discounts() == [("alfa", 1000), ("beta", 900)]
    assert c.total_cents() == 8100


def test_percent_rounds_half_up():
    c = Cart()
    c.add_item("GUM", "Gum", 25)
    c.add_discount("HALF", "percent", 50)
    assert c.applied_discounts() == [("HALF", 13)]  # 12.5 rounds up
    assert c.total_cents() == 12

    c2 = Cart()
    c2.add_item("W", "Widget", 3333, qty=3)  # 9999
    c2.add_discount("TEN", "percent", 10)
    assert c2.total_cents() == 8999


def test_exclusive_suppresses_stackables():
    c = make_cart()
    c.add_discount("HALF", "percent", 50, priority=1)
    c.add_discount("VIP", "fixed", 2000, priority=99, exclusive=True)
    assert c.applied_discounts() == [("VIP", 2000)]
    assert c.total_cents() == 8000


def test_best_exclusive_wins():
    c = make_cart()
    c.add_discount("F2000", "fixed", 2000, priority=1, exclusive=True)
    c.add_discount("P25", "percent", 25, priority=9, exclusive=True)
    assert c.applied_discounts() == [("P25", 2500)]
    assert c.total_cents() == 7500

    c2 = make_cart()
    c2.add_discount("P15", "percent", 15, priority=1, exclusive=True)
    c2.add_discount("F2000", "fixed", 2000, priority=9, exclusive=True)
    assert c2.applied_discounts() == [("F2000", 2000)]
    assert c2.total_cents() == 8000


def test_exclusive_savings_tie_breaks_by_priority_then_code():
    c = make_cart()
    c.add_discount("zz", "percent", 20, priority=3, exclusive=True)
    c.add_discount("aa", "fixed", 2000, priority=5, exclusive=True)
    assert c.applied_discounts() == [("zz", 2000)]

    c2 = make_cart()
    c2.add_discount("mm", "percent", 20, priority=4, exclusive=True)
    c2.add_discount("aa", "fixed", 2000, priority=4, exclusive=True)
    assert c2.applied_discounts() == [("aa", 2000)]


def test_total_never_goes_negative():
    c = Cart()
    c.add_item("PEN", "Pen", 500)
    c.add_discount("a", "fixed", 400, priority=1)
    c.add_discount("b", "fixed", 400, priority=2)
    assert c.applied_discounts() == [("a", 400), ("b", 100)]
    assert c.total_cents() == 0

    empty = Cart()
    empty.add_discount("x", "fixed", 500)
    assert empty.total_cents() == 0
    assert empty.applied_discounts() == [("x", 0)]


def test_discount_validation():
    c = make_cart()
    c.add_discount("OK", "percent", 10)
    bad = [
        ("OK", "percent", 5),      # duplicate code
        ("B1", "bogo", 5),         # unknown kind
        ("B2", "percent", 0),      # percent out of range
        ("B3", "percent", 101),
        ("B4", "percent", 10.5),   # not an int
        ("B5", "fixed", 0),        # fixed must be >= 1 cent
    ]
    for code, kind, value in bad:
        try:
            c.add_discount(code, kind, value)
            assert False, "accepted bad discount %r" % ((code, kind, value),)
        except ValueError:
            pass


EXISTING = [
    test_add_accumulates_same_sku,
    test_re_add_with_different_details_rejected,
    test_remove_item,
    test_total_matches_subtotal_and_receipt,
    test_format_cents,
    test_item_validation,
]

FEATURE = [
    test_discounts_stack_in_priority_order,
    test_priority_ties_break_by_code,
    test_percent_rounds_half_up,
    test_exclusive_suppresses_stackables,
    test_best_exclusive_wins,
    test_exclusive_savings_tie_breaks_by_priority_then_code,
    test_total_never_goes_negative,
    test_discount_validation,
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
