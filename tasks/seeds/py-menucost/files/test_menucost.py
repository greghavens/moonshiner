"""Acceptance tests for the order-desk pricing module. Run: python3 test_menucost.py"""

import menucost

ROWS = [
    {"sku": "brisket-plate", "qty": 2, "unit_cost": 6.40},
    {"sku": "slaw-side", "qty": 1, "unit_cost": 2.25},
    {"sku": "brisket-plate", "qty": 1, "unit_cost": 6.40},
    {"sku": "cornbread", "qty": 3, "unit_cost": 1.60},
]


def test_plate_costs_merge_duplicate_rows():
    costs = menucost.plate_costs(ROWS)
    assert round(costs["brisket-plate"], 2) == 19.20, costs
    assert round(costs["slaw-side"], 2) == 2.25, costs
    assert round(costs["cornbread"], 2) == 4.80, costs
    assert set(costs) == {"brisket-plate", "slaw-side", "cornbread"}, costs


def test_menu_prices_apply_house_margin_per_sku():
    prices = menucost.menu_prices(ROWS)
    assert prices["brisket-plate"] == 25.92, prices
    assert prices["slaw-side"] == 3.04, prices
    assert prices["cornbread"] == 6.48, prices


def test_menu_prices_values_are_cents_rounded_floats():
    for sku, price in menucost.menu_prices(ROWS).items():
        assert isinstance(price, float), (sku, price)
        assert round(price, 2) == price, (sku, price)


def test_quote_single_sku():
    assert menucost.quote(ROWS, "cornbread") == 6.48


def test_party_subtotal_and_total():
    assert menucost.party_subtotal(ROWS, ["brisket-plate", "cornbread"]) == 32.40
    assert menucost.party_total(ROWS, ["brisket-plate", "cornbread"]) == 36.45


def test_empty_order():
    assert menucost.menu_prices([]) == {}
    assert menucost.party_subtotal(ROWS, []) == 0.0


def main():
    test_plate_costs_merge_duplicate_rows()
    test_menu_prices_apply_house_margin_per_sku()
    test_menu_prices_values_are_cents_rounded_floats()
    test_quote_single_sku()
    test_party_subtotal_and_total()
    test_empty_order()
    print("ok - menucost")


if __name__ == "__main__":
    main()
