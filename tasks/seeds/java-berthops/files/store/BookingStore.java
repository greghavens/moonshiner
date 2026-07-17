package store;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;

import domain.Booking;
import domain.PortError;

/** Booking ledger. Ids are issued in strict sequence so exports stay stable. */
public final class BookingStore {
    private final LinkedHashMap<String, Booking> byId = new LinkedHashMap<>();
    private int nextSeq = 1;

    public String nextId() {
        String id = "BK-" + nextSeq;
        nextSeq++;
        return id;
    }

    public void save(Booking booking) {
        byId.put(booking.id(), booking);
    }

    public Booking require(String id) {
        Booking booking = byId.get(id);
        if (booking == null) {
            throw new PortError("UNKNOWN_BOOKING", "no booking on file: " + id);
        }
        return booking;
    }

    /** Active bookings for one berth, oldest first. */
    public List<Booking> activeForBerth(String berthCode) {
        List<Booking> out = new ArrayList<>();
        for (Booking booking : byId.values()) {
            if (booking.isActive() && booking.berthCode().equals(berthCode)) {
                out.add(booking);
            }
        }
        return out;
    }

    /** Reflag support: every booking follows the vessel to its new name. */
    public void retagVessel(String oldName, String newName) {
        for (Booking booking : byId.values()) {
            if (booking.vesselName().equals(oldName)) {
                booking.retagVessel(newName);
            }
        }
    }
}
