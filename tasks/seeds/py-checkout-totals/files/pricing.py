"""Order pricing for the web checkout.

Business rules (per the pricing spec, PRC-114):
  * the subtotal is the sum of the line prices; every line price is a
    two-decimal dollar amount that already includes per-line discounts
  * orders whose subtotal reaches $50.00 ship free, everything else pays
    the $6.95 flat rate
  * an invoice is settled once its recorded payments cover the total
    exactly, to the cent
"""

FREE_SHIPPING_AT = 50.00
FLAT_SHIPPING = 6.95


def subtotal(prices):
    """Sum of the line prices, in dollars."""
    total = 0.0
    for price in prices:
        total += price
    return total


def shipping(prices):
    """Shipping charge for the order: free at/above the threshold."""
    if subtotal(prices) >= FREE_SHIPPING_AT:
        return 0.0
    return FLAT_SHIPPING


def order_total(prices):
    """What the customer is charged: subtotal plus shipping."""
    return subtotal(prices) + shipping(prices)


def is_settled(total_due, payments):
    """True once *payments* (dollar amounts) cover *total_due* exactly."""
    return sum(payments) == total_due


def outstanding(total_due, payments):
    """Dollars still owed on the invoice (never negative)."""
    remaining = total_due - sum(payments)
    return max(remaining, 0.0)
