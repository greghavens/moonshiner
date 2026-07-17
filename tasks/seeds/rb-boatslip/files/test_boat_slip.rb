require "minitest/autorun"
require_relative "boat_slip"

class BoatSlipTest < Minitest::Test
  def rental
    Rental.new(
      renter: "Mara Voss",
      boat: "Heron 16",
      slip: "D-4",
      date: "2026-07-04",
      hours: 3,
      rate: 22.5,
      card_tail: "4417"
    )
  end

  def test_total_charge
    assert_in_delta 67.5, BoatSlip.total(rental), 0.001
  end

  def test_confirmation_letter_is_filled_in_and_flush_left
    expected = "Ahoy Mara Voss!\n" \
               "\n" \
               "Your rental of Heron 16 is confirmed.\n" \
               "Slip D-4, 2026-07-04, 3 hours.\n" \
               "\n" \
               "Please check in at the office fifteen minutes early.\n"
    assert_equal expected, BoatSlip.confirmation(rental)
  end

  def test_confirmation_has_no_leading_indentation_anywhere
    refute_match(/^[ \t]/, BoatSlip.confirmation(rental))
  end

  def test_receipt_is_filled_in_and_flush_left
    expected = "RECEIPT — 2026-07-04\n" \
               "Heron 16, 3h @ $22.50/h\n" \
               "Card ending 4417 charged $67.50\n"
    assert_equal expected, BoatSlip.receipt(rental)
  end

  def test_receipt_never_prints_template_holes
    refute_includes BoatSlip.receipt(rental), "\#{"
  end
end
