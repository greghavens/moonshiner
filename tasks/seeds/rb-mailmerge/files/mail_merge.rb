# Renders order-confirmation emails for the Riverbend Playhouse box office.
# The mailer daemon calls TicketMailer#render once per paid order and pipes
# the result straight to sendmail, so what render returns is exactly what
# the customer reads.
class TicketMailer
  Order = Struct.new(:first_name, :show, :date, :time, :qty, :seats, :total, :ref,
                     keyword_init: true)

  def render(order)
    "Subject: #{subject(order)}\n\n#{greeting(order)}\n\n#{body(order)}"
  end

  def subject(order)
    %q(Riverbend Playhouse — #{order.show}, order #{order.ref})
  end

  def greeting(order)
    'Dear #{order.first_name},'
  end

  def body(order)
    <<-BODY
      Your #{order.qty} seat(s) for #{order.show} are confirmed for
      #{order.date} at #{order.time}.

      Seats: #{order.seats.join(', ')}
      Total charged: #{format('$%.2f', order.total)}

      Doors open 30 minutes before curtain. Show this email or give
      the code #{order.ref} at will call.
    BODY
  end
end
