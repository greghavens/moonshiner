import java.util.Objects;

/**
 * One hold in the reservation book: a patron on a specific seat, or a
 * general-admission hold when the performance sells unassigned standing
 * room (seatNumber is null in that case).
 */
public final class Reservation {
    private final String patron;
    private final Integer seatNumber;   // null = general admission, no assigned seat

    public Reservation(String patron, Integer seatNumber) {
        this.patron = Objects.requireNonNull(patron, "patron");
        this.seatNumber = seatNumber;
    }

    public String patron() { return patron; }
    public Integer seatNumber() { return seatNumber; }

    @Override
    public String toString() {
        return patron + (seatNumber == null ? " [GA]" : " [seat " + seatNumber + "]");
    }
}
