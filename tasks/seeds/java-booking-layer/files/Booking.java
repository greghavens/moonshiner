import java.util.Objects;

/** Aggregate that owns a booked stay and its locked commercial terms. */
public final class Booking {
    private final String id;
    private final String guestName;
    private int nights;
    private long nightlyCents;
    private long totalCents;
    private final String status;

    private Booking(String id, String guestName, int nights, long nightlyCents,
                    long totalCents, String status) {
        this.id = Objects.requireNonNull(id);
        this.guestName = Objects.requireNonNull(guestName);
        this.nights = nights;
        this.nightlyCents = nightlyCents;
        this.totalCents = totalCents;
        this.status = Objects.requireNonNull(status);
    }

    public static Booking restore(String id, String guestName, int nights, long nightlyCents,
                                  long totalCents, String status) {
        return new Booking(id, guestName, nights, nightlyCents, totalCents, status);
    }

    /** Extends a confirmed stay after the application layer obtains today's quote. */
    public void extendStay(int additionalNights, long currentNightlyCents) {
        if (additionalNights <= 0) {
            throw new IllegalArgumentException("additional nights must be positive");
        }
        if (!status.equals("CONFIRMED")) {
            throw new IllegalStateException("only confirmed bookings can be extended");
        }
        int newNights = Math.addExact(nights, additionalNights);
        long newTotal = Math.multiplyExact((long) newNights, currentNightlyCents);
        nights = newNights;
        nightlyCents = currentNightlyCents;
        totalCents = newTotal;
    }

    public String id() { return id; }
    public String guestName() { return guestName; }
    public int nights() { return nights; }
    public long nightlyCents() { return nightlyCents; }
    public long totalCents() { return totalCents; }
    public String status() { return status; }
}
