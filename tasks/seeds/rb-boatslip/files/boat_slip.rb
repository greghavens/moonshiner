# Prints the paper slips we hand renters at the marina office: a
# confirmation letter at booking time and a receipt at checkout.

Rental = Struct.new(:renter, :boat, :slip, :date, :hours, :rate, :card_tail,
                    keyword_init: true)

module BoatSlip
  module_function

  # Total charge in dollars for a rental.
  def total(rental)
    (rental.hours * rental.rate).round(2)
  end

  # The confirmation letter, ready to print — flush against the left
  # margin, the office printer has no patience for stray indentation.
  def confirmation(rental)
    letter = <<LETTER
      Ahoy #{rental.renter}!

      Your rental of #{rental.boat} is confirmed.
      Slip #{rental.slip}, #{rental.date}, #{rental.hours} hours.

      Please check in at the office fifteen minutes early.
      LETTER
    letter
  end

  # The checkout receipt. Same deal: left margin, filled in from the rental.
  def receipt(rental)
    <<~'RECEIPT'
      RECEIPT — #{rental.date}
      #{rental.boat}, #{rental.hours}h @ $#{format('%.2f', rental.rate)}/h
      Card ending #{rental.card_tail} charged $#{format('%.2f', total(rental))}
    RECEIPT
  end
end
