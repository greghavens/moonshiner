"""Behavior pins for the dye-lot batching module."""

import dyelot


def test_registry_carries_all_fibers():
    assert set(dyelot.RECIPES) == {"wool", "cotton", "silk"}


def test_wool_recipe():
    assert dyelot.batch_grams("wool", 10) == 42.0
    assert dyelot.batch_grams("wool", 5, 1.0) == 21.0


def test_cotton_recipe_includes_strike_bath():
    assert dyelot.batch_grams("cotton", 8) == 47.8
    assert dyelot.batch_grams("cotton", 1, 2.0) == 14.2


def test_silk_deep_shades_get_mordant_bump():
    assert dyelot.batch_grams("silk", 6) == 17.4
    assert dyelot.batch_grams("silk", 6, 2.0) == 38.3


def test_unknown_fiber_is_a_key_error():
    try:
        dyelot.batch_grams("ramie", 4)
    except KeyError:
        pass
    else:
        raise AssertionError("unknown fiber should raise KeyError")


def test_split_lot_even_and_remainder():
    assert dyelot.split_lot(12, 4) == [4, 4, 4]
    assert dyelot.split_lot(23, 6) == [6, 6, 6, 5]
    assert dyelot.split_lot(3, 5) == [3]
    assert dyelot.split_lot(0, 5) == []


def test_lot_sheet_rows_follow_the_split():
    assert dyelot.lot_sheet("wool", 13, 5) == [(5, 21.0), (5, 21.0), (3, 12.6)]
    assert dyelot.lot_sheet("cotton", 4, 4, 1.0) == [(4, 25.4)]


def main():
    test_registry_carries_all_fibers()
    test_wool_recipe()
    test_cotton_recipe_includes_strike_bath()
    test_silk_deep_shades_get_mordant_bump()
    test_unknown_fiber_is_a_key_error()
    test_split_lot_even_and_remainder()
    test_lot_sheet_rows_follow_the_split()
    print("ok")


if __name__ == "__main__":
    main()
