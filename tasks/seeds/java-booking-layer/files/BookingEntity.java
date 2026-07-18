/** Stable persistence record used by the booking table adapter. */
public record BookingEntity(
        String id,
        String guestName,
        int nights,
        long nightlyCents,
        long totalCents,
        String status) {}
