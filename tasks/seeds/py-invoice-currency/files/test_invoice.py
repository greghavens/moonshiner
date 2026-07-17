"""Acceptance checks for invoice.py. Run: python3 test_invoice.py"""
from invoice import Invoice, LineItem, format_money, render


# ---------------------------------------------------------------- existing

def test_line_item_math_and_validation():
    assert LineItem("Widget", 2, 19.99).total() == 39.98
    assert LineItem("Hour", 3, 100.0).total() == 300.0
    try:
        LineItem("x", 0, 1.0)
        assert False, "qty 0 accepted"
    except ValueError:
        pass
    try:
        LineItem("x", 1, -0.01)
        assert False, "negative price accepted"
    except ValueError:
        pass


def test_invoice_totals_and_chaining():
    inv = Invoice("INV-1042", "Acme Corp", tax_rate=0.0825)
    inv.add("Widget", 2, 19.99).add("Shipping", 1, 5.0)
    assert float(inv.subtotal()) == 44.98
    assert float(inv.tax()) == 3.71
    assert float(inv.total()) == 48.69
    try:
        Invoice("", "Acme")
        assert False, "empty invoice number accepted"
    except ValueError:
        pass


def test_render_usd_exact():
    inv = Invoice("INV-1042", "Acme Corp", tax_rate=0.0825)
    inv.add("Widget", 2, 19.99).add("Shipping", 1, 5.0)
    expected = (
        "Invoice INV-1042\n"
        "Customer: Acme Corp\n"
        "Date: 2026-06-30\n"
        "\n"
        "  Widget                   2 x $19.99             $39.98\n"
        "  Shipping                 1 x $5.00               $5.00\n"
        "\n"
        "Subtotal: $44.98\n"
        "Tax: $3.71\n"
        "Total: $48.69"
    )
    assert render(inv, issued="2026-06-30") == expected


def test_render_skips_tax_line_when_untaxed():
    inv = Invoice("INV-7", "Bob's Bikes").add("Consulting", 1, 150.0)
    out = render(inv, issued="2026-06-30")
    assert "Tax:" not in out
    assert "Total: $150.00" in out


def test_format_money_default():
    assert format_money(0) == "$0.00"
    assert format_money(1234.5) == "$1234.50"


# ------------------------------------------------- feature: multi-currency

def test_currency_registry_and_default():
    from invoice import CURRENCIES
    assert {"USD", "EUR", "JPY", "CHF"} <= set(CURRENCIES)
    inv = Invoice("INV-1", "Acme Corp")
    assert inv.currency == "USD"
    try:
        Invoice("INV-2", "Oz Pty", currency="AUD")
        assert False, "unknown currency code accepted"
    except ValueError:
        pass


def test_format_money_per_currency():
    assert format_money(3600, "JPY") == "¥3600"
    assert format_money(7.05, "CHF") == "CHF 7.05"
    assert format_money(1.5, "EUR") == "€1.50"
    assert format_money(2.5) == "$2.50"


def test_jpy_has_no_minor_unit():
    inv = Invoice("INV-JP-1", "Tokyo KK", tax_rate=0.1, currency="JPY")
    inv.add("Consulting", 3, 1200)
    assert float(inv.subtotal()) == 3600.0
    assert float(inv.tax()) == 360.0
    assert float(inv.total()) == 3960.0
    out = render(inv, issued="2026-06-30")
    assert "Subtotal: ¥3600" in out
    assert "Total: ¥3960" in out
    assert "¥3960.00" not in out


def test_jpy_rounds_half_up_to_whole_yen():
    inv = Invoice("INV-JP-2", "Tokyo KK", currency="JPY")
    inv.add("Adapter", 2, 216.25)
    # line total 432.50 -> 433 (half always rounds up, never to even)
    assert float(inv.total()) == 433.0


def test_usd_rounds_half_up_at_cents():
    inv = Invoice("INV-3", "Acme Corp", currency="USD")
    inv.add("Bolt", 3, 0.335)
    # 3 x 0.335 = 1.005 -> $1.01
    assert float(inv.total()) == 1.01


def test_lines_round_before_summing():
    inv = Invoice("INV-4", "Acme Corp")
    inv.add("Bolt A", 1, 0.125).add("Bolt B", 1, 0.125)
    # each line rounds half-up to 0.13 first; the subtotal sums rounded lines
    assert float(inv.subtotal()) == 0.26


def test_chf_rounds_to_five_rappen():
    for price, want in [(7.02, 7.0), (7.03, 7.05), (7.025, 7.05), (7.10, 7.10)]:
        inv = Invoice("INV-CH", "Zurich AG", currency="CHF")
        inv.add("Fondue set", 1, price)
        got = float(inv.total())
        assert got == want, "CHF %s -> %s, want %s" % (price, got, want)
    out = render(inv, issued="2026-06-30")
    assert "Total: CHF 7.10" in out


EXISTING = [
    test_line_item_math_and_validation,
    test_invoice_totals_and_chaining,
    test_render_usd_exact,
    test_render_skips_tax_line_when_untaxed,
    test_format_money_default,
]

FEATURE = [
    test_currency_registry_and_default,
    test_format_money_per_currency,
    test_jpy_has_no_minor_unit,
    test_jpy_rounds_half_up_to_whole_yen,
    test_usd_rounds_half_up_at_cents,
    test_lines_round_before_summing,
    test_chf_rounds_to_five_rappen,
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
