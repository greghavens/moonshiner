"""Behavior checks for checkout pricing. Run: python3 test_pricing.py"""
from pricing import is_settled, order_total, outstanding, shipping, subtotal


def main():
    # Cents-exact subtotal.
    assert round(subtotal([12.50, 3.99]), 2) == 16.49

    # A cart summing to exactly $50.00 ships free.
    at_threshold = [9.00, 25.24, 15.76]
    assert round(subtotal(at_threshold), 2) == 50.00
    got = shipping(at_threshold)
    assert got == 0.0, f"a $50.00 cart must ship free, got shipping={got!r}"
    assert round(order_total(at_threshold), 2) == 50.00, (
        f"customer must be charged $50.00, got {order_total(at_threshold)!r}")

    # One cent under the threshold still pays flat-rate shipping.
    just_under = [9.00, 25.24, 15.75]
    got = shipping(just_under)
    assert got == 6.95, f"a $49.99 cart pays shipping, got {got!r}"
    assert round(order_total(just_under), 2) == 56.94

    # Comfortably above and below behave, too.
    assert shipping([60.00]) == 0.0
    assert round(order_total([10.00]), 2) == 16.95

    # An invoice paid in three equal cent-exact installments is settled.
    assert is_settled(100.11, [33.37, 33.37, 33.37]) is True, (
        "three payments of $33.37 must settle a $100.11 invoice")
    assert round(outstanding(100.11, [33.37, 33.37, 33.37]), 2) == 0.00, (
        f"nothing should be owed, got {outstanding(100.11, [33.37, 33.37, 33.37])!r}")

    # Underpayment is not settled, and the balance is right.
    assert is_settled(100.11, [33.37, 33.37]) is False
    assert round(outstanding(100.11, [33.37, 33.37]), 2) == 33.37

    # Overpayment is not "exactly covered" either, and owes nothing.
    assert is_settled(100.11, [33.37, 33.37, 33.38]) is False
    assert outstanding(100.11, [33.37, 33.37, 33.38]) == 0.0

    # Nothing paid yet.
    assert is_settled(20.00, []) is False
    assert round(outstanding(20.00, []), 2) == 20.00

    print("all checks passed")


if __name__ == "__main__":
    main()
