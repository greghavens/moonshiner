require "minitest/autorun"
require_relative "mail_merge"

# Acceptance tests for the box-office confirmation mailer. The rendered
# string goes to customers verbatim, so the full text is pinned exactly.
# Run: ruby test_mail_merge.rb
class TicketMailerTest < Minitest::Test
  def mailer
    TicketMailer.new
  end

  def maya
    TicketMailer::Order.new(
      first_name: "Maya", show: "The Winter's Tale", date: "Sat, Aug 22",
      time: "7:30 PM", qty: 2, seats: %w[G4 G5], total: 44.0, ref: "RP-58121"
    )
  end

  def ronan
    TicketMailer::Order.new(
      first_name: "Rónán", show: "Twelfth Night", date: "Sun, Aug 23",
      time: "2:00 PM", qty: 1, seats: %w[C12], total: 22.5, ref: "RP-58200"
    )
  end

  def test_two_seat_confirmation_reads_exactly_as_designed
    expected = <<~EMAIL
      Subject: Riverbend Playhouse — The Winter's Tale, order RP-58121

      Dear Maya,

      Your 2 seat(s) for The Winter's Tale are confirmed for
      Sat, Aug 22 at 7:30 PM.

      Seats: G4, G5
      Total charged: $44.00

      Doors open 30 minutes before curtain. Show this email or give
      the code RP-58121 at will call.
    EMAIL
    assert_equal expected, mailer.render(maya)
  end

  def test_single_seat_confirmation_reads_exactly_as_designed
    expected = <<~EMAIL
      Subject: Riverbend Playhouse — Twelfth Night, order RP-58200

      Dear Rónán,

      Your 1 seat(s) for Twelfth Night are confirmed for
      Sun, Aug 23 at 2:00 PM.

      Seats: C12
      Total charged: $22.50

      Doors open 30 minutes before curtain. Show this email or give
      the code RP-58200 at will call.
    EMAIL
    assert_equal expected, mailer.render(ronan)
  end

  def test_no_template_placeholders_leak_into_the_email
    refute_includes mailer.render(maya), '#{'
  end

  def test_greeting_addresses_the_customer_by_name
    assert_includes mailer.render(maya), "Dear Maya,"
    assert_includes mailer.render(ronan), "Dear Rónán,"
  end

  def test_subject_line_names_the_show_and_order
    assert_includes mailer.render(maya).lines.first,
                    "The Winter's Tale, order RP-58121"
  end

  def test_body_is_flush_left_for_plain_text_clients
    mailer.render(maya).lines.each do |line|
      refute line.start_with?(" ", "\t"),
             "line is indented: #{line.inspect}"
    end
  end
end
