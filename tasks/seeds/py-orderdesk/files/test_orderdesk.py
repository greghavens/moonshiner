"""Acceptance checks for the orderdesk package split.

Run: python3 test_orderdesk.py

These tests import the post-split layout: an ``orderdesk`` PACKAGE with
``pricing``, ``tax``, ``shipping`` and ``report`` modules and a facade
``__init__`` that keeps every existing call site working unchanged. All
behavior pins below were captured from the module before the split — the
numbers and report text must not move.
"""
import os

import orderdesk
from orderdesk import pricing, report, shipping, tax


HERE = os.path.dirname(os.path.abspath(__file__))

ORDER_A = {
    "id": 2041,
    "customer": {"name": "Cedar Grocery Co-op", "tax_exempt": False},
    "lines": [
        {"sku": "RICE-25KG", "category": "food",
         "unit_cents": 4500, "qty": 4, "weight_g": 25000},
        {"sku": "MUG-CLASSIC", "category": "standard",
         "unit_cents": 850, "qty": 12, "weight_g": 400},
        {"sku": "COOKBOOK-2", "category": "books",
         "unit_cents": 2199, "qty": 1, "weight_g": 900},
    ],
}

ORDER_B = {
    "id": 2042,
    "customer": {"name": "Juniper Stationers", "tax_exempt": False},
    "lines": [
        {"sku": "PEN-GEL-BLK", "category": "standard",
         "unit_cents": 130, "qty": 1, "weight_g": 40},
        {"sku": "PEN-GEL-BLU", "category": "standard",
         "unit_cents": 130, "qty": 1, "weight_g": 40},
    ],
}

REPORT_B = (
    "ORDER 2042 — Juniper Stationers\n"
    "SKU          QTY  LINE TOTAL\n"
    "PEN-GEL-BLK    1       $1.30\n"
    "PEN-GEL-BLU    1       $1.30\n"
    "----------------------------\n"
    "Subtotal               $2.60\n"
    "Tax                    $0.20\n"
    "Shipping               $4.95\n"
    "TOTAL                  $7.75\n"
)


def _order(lines, exempt=False):
    return {"id": 1, "customer": {"name": "T", "tax_exempt": exempt},
            "lines": lines}


def _line(qty=1, unit=100, cat="standard", weight=100):
    return {"sku": "X", "category": cat, "unit_cents": unit,
            "qty": qty, "weight_g": weight}


# ----------------------------------------------------------------- layout

def test_orderdesk_is_a_package_and_the_old_module_is_gone():
    assert os.path.basename(orderdesk.__file__) == "__init__.py", \
        "orderdesk must be a package (orderdesk/__init__.py), got %r" % orderdesk.__file__
    assert not os.path.exists(os.path.join(HERE, "orderdesk.py")), \
        "the old single-file orderdesk.py must be removed"


def test_each_domain_module_owns_its_functions():
    assert callable(pricing.line_total_cents)
    assert callable(pricing.price_lines)
    assert callable(pricing.subtotal_cents)
    assert callable(tax.tax_cents)
    assert callable(shipping.shipping_cents)
    assert callable(shipping.total_weight_g)
    assert callable(report.render_report)


def test_facade_reexports_are_the_same_objects():
    assert orderdesk.price_lines is pricing.price_lines
    assert orderdesk.subtotal_cents is pricing.subtotal_cents
    assert orderdesk.line_total_cents is pricing.line_total_cents
    assert orderdesk.tax_cents is tax.tax_cents
    assert orderdesk.shipping_cents is shipping.shipping_cents
    assert orderdesk.total_weight_g is shipping.total_weight_g
    assert orderdesk.render_report is report.render_report
    assert callable(orderdesk.order_total)


def test_rate_tables_live_with_their_modules():
    assert [tuple(b) for b in pricing.PRICE_BREAKS] == [(50, 10), (10, 5)]
    assert tax.TAX_RATES_BP["standard"] == 825
    assert [tuple(b) for b in shipping.SHIPPING_BANDS] == \
        [(500, 495), (2000, 795), (10000, 1495)]
    assert shipping.FREE_SHIPPING_MIN_CENTS == 15000


# --------------------------------------------------------------- behavior

def test_price_lines_and_quantity_breaks():
    assert orderdesk.price_lines(ORDER_A) == [
        ("RICE-25KG", 18000), ("MUG-CLASSIC", 9690), ("COOKBOOK-2", 2199)]
    assert pricing.line_total_cents(_line(qty=9)) == 900
    assert pricing.line_total_cents(_line(qty=10)) == 950
    assert pricing.line_total_cents(_line(qty=49)) == 4655
    assert pricing.line_total_cents(_line(qty=50)) == 4500


def test_tax_is_per_line_floored_on_discounted_totals():
    # two 130c lines: 10c tax each, NOT 21c computed on the 260c subtotal
    assert orderdesk.tax_cents(ORDER_B) == 20
    assert orderdesk.tax_cents(ORDER_A) == 799
    assert tax.tax_cents(_order([_line(unit=1000)], exempt=True)) == 0
    # unknown categories fall back to the standard rate
    assert tax.tax_cents(_order([_line(unit=1000, cat="gadgets")])) == 82


def test_shipping_bands_and_free_threshold():
    for weight, want in [(500, 495), (501, 795), (2000, 795),
                         (2001, 1495), (10000, 1495), (10001, 2495)]:
        got = shipping.shipping_cents(_order([_line(unit=10, weight=weight)]))
        assert got == want, "weight %d: got %d, want %d" % (weight, got, want)
    assert shipping.shipping_cents(_order([_line(unit=15000)])) == 0
    assert shipping.shipping_cents(_order([_line(unit=14999)])) == 495


def test_order_total_composes_the_three_domains():
    assert orderdesk.order_total(ORDER_A) == {
        "subtotal_cents": 29889, "tax_cents": 799,
        "shipping_cents": 0, "total_cents": 30688}
    assert orderdesk.order_total(ORDER_B) == {
        "subtotal_cents": 260, "tax_cents": 20,
        "shipping_cents": 495, "total_cents": 775}
    assert orderdesk.order_total(_order([])) == {
        "subtotal_cents": 0, "tax_cents": 0,
        "shipping_cents": 0, "total_cents": 0}


def test_report_text_is_byte_for_byte_stable():
    assert orderdesk.render_report(ORDER_B) == REPORT_B
    got = orderdesk.render_report(ORDER_A)
    assert got.startswith("ORDER 2041 — Cedar Grocery Co-op\n")
    assert "MUG-CLASSIC   12      $96.90\n" in got
    assert got.endswith("TOTAL                $306.88\n")


CHECKS = [
    test_orderdesk_is_a_package_and_the_old_module_is_gone,
    test_each_domain_module_owns_its_functions,
    test_facade_reexports_are_the_same_objects,
    test_rate_tables_live_with_their_modules,
    test_price_lines_and_quantity_breaks,
    test_tax_is_per_line_floored_on_discounted_totals,
    test_shipping_bands_and_free_threshold,
    test_order_total_composes_the_three_domains,
    test_report_text_is_byte_for_byte_stable,
]


def main_check():
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
    main_check()
