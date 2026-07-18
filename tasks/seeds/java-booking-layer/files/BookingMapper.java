/** Mapping boundary between domain, persistence, and transport records. */
public final class BookingMapper {
    public Booking fromEntity(BookingEntity entity) {
        return Booking.restore(entity.id(), entity.guestName(), entity.nights(),
                entity.nightlyCents(), entity.totalCents(), entity.status());
    }

    public BookingEntity toEntity(Booking booking) {
        return new BookingEntity(booking.id(), booking.guestName(), booking.nights(),
                booking.nightlyCents(), booking.totalCents(), booking.status());
    }

    public BookingDto toDto(Booking booking) {
        return new BookingDto(booking.id(), booking.guestName(), booking.nights(),
                booking.nightlyCents(), booking.totalCents(), booking.status());
    }
}
