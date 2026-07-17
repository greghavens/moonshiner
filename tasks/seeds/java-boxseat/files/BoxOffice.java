import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Reservation book for a single performance. Seat numbers come from the
 * hall's seating chart (the studio runs 1-80, the main hall 101-412);
 * general-admission holds carry no seat at all.
 */
public final class BoxOffice {
    private final String performance;
    private final List<Reservation> book = new ArrayList<>();

    public BoxOffice(String performance) {
        this.performance = Objects.requireNonNull(performance, "performance");
    }

    public String performance() { return performance; }

    /**
     * Books a seat for a patron, refusing to double-book. A null seat is a
     * general-admission hold and is always accepted. Returns true when the
     * hold was written into the book.
     */
    public boolean reserve(String patron, Integer seat) {
        Objects.requireNonNull(patron, "patron");
        if (seat != null && isTaken(seat)) {
            return false;
        }
        book.add(new Reservation(patron, seat));
        return true;
    }

    /** Is this seat already held? General admission never blocks a seat. */
    public boolean isTaken(Integer seat) {
        if (seat == null) {
            return false;
        }
        for (Reservation r : book) {
            if (r.seatNumber() != null && r.seatNumber() == seat) {
                return true;
            }
        }
        return false;
    }

    /** Who holds this seat, or null when it is free. */
    public String patronFor(Integer seat) {
        if (seat == null) {
            return null;
        }
        for (Reservation r : book) {
            if (r.seatNumber() != null && r.seatNumber() == seat) {
                return r.patron();
            }
        }
        return null;
    }

    /**
     * Cancels the hold on a seat (refund desk). Returns true when a hold
     * was found and released.
     */
    public boolean release(Integer seat) {
        if (seat == null) {
            return false;
        }
        for (int i = 0; i < book.size(); i++) {
            Reservation r = book.get(i);
            if (r.seatNumber() != null && r.seatNumber() == seat) {
                book.remove(i);
                return true;
            }
        }
        return false;
    }

    /** Number of holds in the book, general admission included. */
    public int holds() {
        return book.size();
    }
}
