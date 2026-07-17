package api;

import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;

import domain.Booking;
import store.AvailabilityCache;
import store.BerthStore;
import store.BookingStore;

/** Read side of the desk: the availability board agents quote from. */
public final class BoardView {
    private final BerthStore berths;
    private final BookingStore bookings;
    private final AvailabilityCache cache;

    public BoardView(BerthStore berths, BookingStore bookings, AvailabilityCache cache) {
        this.berths = berths;
        this.bookings = bookings;
        this.cache = cache;
    }

    /** Free hour slots on a berth inside [fromSlot, toSlot), ascending. */
    public List<Integer> freeSlots(String berthCode, int fromSlot, int toSlot) {
        berths.require(berthCode);
        Validation.requireWindow(fromSlot, toSlot);
        TreeSet<Integer> occupied = cache.occupied(berthCode, () -> occupiedNow(berthCode));
        List<Integer> free = new ArrayList<>();
        for (int slot = fromSlot; slot < toSlot; slot++) {
            if (!occupied.contains(slot)) {
                free.add(slot);
            }
        }
        return free;
    }

    private TreeSet<Integer> occupiedNow(String berthCode) {
        TreeSet<Integer> occupied = new TreeSet<>();
        for (Booking booking : bookings.activeForBerth(berthCode)) {
            occupied.addAll(booking.occupiedSlots());
        }
        return occupied;
    }
}
