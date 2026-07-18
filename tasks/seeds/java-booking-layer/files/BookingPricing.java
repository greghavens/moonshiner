import java.util.HashMap;
import java.util.Map;

/** Adapter for current market quotes used by the application workflow. */
public final class BookingPricing {
    private final Map<String, Long> quotes = new HashMap<>();
    private int calls;

    public void setCurrentNightly(String bookingId, long cents) {
        quotes.put(bookingId, cents);
    }

    public long currentNightly(String bookingId) {
        calls++;
        Long quote = quotes.get(bookingId);
        if (quote == null) throw new IllegalArgumentException("no quote for " + bookingId);
        return quote;
    }

    public int calls() { return calls; }
}
