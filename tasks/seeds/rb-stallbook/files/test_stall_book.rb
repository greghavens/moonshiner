require "minitest/autorun"
require_relative "stall_book"

class StallBookTest < Minitest::Test
  def fixture
    produce = Section.new("Produce", 12.0, [
      Stall.new("Vetch & Vine", [
        Sale.new("heirloom tomatoes", 40.0, true),
        Sale.new("basil bunch", 5.0, false)
      ]),
      Stall.new("Root Cellar", [
        Sale.new("seed potatoes", 25.0, true)
      ])
    ])
    crafts = Section.new("Crafts", 18.0, [
      Stall.new("Tin & Twine", [
        Sale.new("woven basket", 60.0, true),
        Sale.new("candle pair", 22.0, true)
      ])
    ])
    [produce, crafts]
  end

  def test_settlement_rows_skip_unpaid_and_compute_dues
    rows = StallBook.settlement_rows(fixture)
    assert_equal 4, rows.length
    assert_equal(
      ["heirloom tomatoes", "seed potatoes", "woven basket", "candle pair"],
      rows.map { |r| r[:item] }
    )
    first = rows.first
    assert_equal "Produce", first[:section]
    assert_equal "Vetch & Vine", first[:stall]
    assert_in_delta 2.4, first[:due], 0.001
  end

  def test_section_totals_add_table_fees_and_commission
    totals = StallBook.section_totals(fixture)
    assert_equal %w[Produce Crafts], totals.keys
    # Produce: 2 tables * 12.0 + 6% of (40 + 25) paid
    assert_in_delta 27.9, totals["Produce"], 0.001
    # Crafts: 1 table * 18.0 + 6% of (60 + 22)
    assert_in_delta 22.92, totals["Crafts"], 0.001
  end

  def test_section_totals_with_no_paid_sales_is_fees_only
    quiet = [Section.new("Flowers", 10.0, [Stall.new("Late Bloomer", [
      Sale.new("posy", 8.0, false)
    ])])]
    assert_in_delta 10.0, StallBook.section_totals(quiet)["Flowers"], 0.001
  end

  def test_owes_most_names_the_biggest_debtor
    top = StallBook.owes_most(fixture)
    assert_equal "Produce", top[:section]
    assert_in_delta 27.9, top[:due], 0.001
  end

  def test_unpaid_items_for_the_incident_sheet
    assert_equal ["Produce/Vetch & Vine: basil bunch"], StallBook.unpaid_items(fixture)
  end

  def test_empty_market_settles_to_nothing
    assert_equal [], StallBook.settlement_rows([])
    assert_equal({}, StallBook.section_totals([]))
    assert_equal [], StallBook.unpaid_items([])
  end
end
