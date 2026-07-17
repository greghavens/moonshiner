package domain;

import java.util.List;

/** Berthing rules and tariff math from the published port schedule. */
public final class BookingPolicy {
    /** Flat line-handling charge added to every stay, in cents. */
    public static final long LINE_HANDLING_CENTS = 3000L;

    private BookingPolicy() {
    }

    public static void checkFit(Vessel vessel, Berth berth) {
        if (vessel.draftDm() > berth.maxDraftDm()) {
            throw new PortError("VESSEL_TOO_DEEP", vessel.name() + " draws " + vessel.draftDm()
                    + " dm, berth " + berth.code() + " allows " + berth.maxDraftDm() + " dm");
        }
        if (vessel.lengthM() > berth.maxLengthM()) {
            throw new PortError("VESSEL_TOO_LONG", vessel.name() + " is " + vessel.lengthM()
                    + " m, berth " + berth.code() + " allows " + berth.maxLengthM() + " m");
        }
    }

    /**
     * Rejects a window that overlaps any other active booking on the berth.
     * Pass the booking id being re-windowed so it does not conflict with itself.
     */
    public static void checkWindowFree(List<Booking> activeOnBerth, String ignoreBookingId,
            int fromSlot, int toSlot) {
        for (Booking other : activeOnBerth) {
            if (other.id().equals(ignoreBookingId)) {
                continue;
            }
            if (other.overlaps(fromSlot, toSlot)) {
                throw new PortError("WINDOW_CONFLICT", "berth " + other.berthCode() + " held by "
                        + other.id() + " for slots " + other.startSlot() + "-" + other.endSlot());
            }
        }
    }

    public static long quoteCents(Berth berth, int hours) {
        return berth.rateCentsPerHour() * hours + LINE_HANDLING_CENTS;
    }
}
