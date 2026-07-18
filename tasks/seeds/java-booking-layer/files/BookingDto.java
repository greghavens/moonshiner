/** Stable transport record shared by the REST and CLI adapters. */
public record BookingDto(
        String id,
        String guestName,
        int nights,
        long nightlyCents,
        long totalCents,
        String status) {}
