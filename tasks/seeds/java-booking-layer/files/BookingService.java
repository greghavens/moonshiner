/** Application workflow shared by REST and CLI. */
public final class BookingService {
    private final BookingStore store;
    private final BookingMapper mapper;
    private final BookingPricing pricing;

    public BookingService(BookingStore store, BookingMapper mapper, BookingPricing pricing) {
        this.store = store;
        this.mapper = mapper;
        this.pricing = pricing;
    }

    public BookingDto extend(String bookingId, int additionalNights) {
        Booking booking = mapper.fromEntity(store.load(bookingId));
        long currentQuote = pricing.currentNightly(bookingId);
        booking.extendStay(additionalNights, currentQuote);
        store.save(mapper.toEntity(booking));
        return mapper.toDto(booking);
    }
}
