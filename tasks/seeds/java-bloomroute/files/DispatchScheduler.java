import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZoneOffset;

/**
 * Turns an order timestamp into a delivery promise. Orders placed before
 * the shop's local 2 pm cutoff go out the same day; later orders go out
 * the next day.
 */
public final class DispatchScheduler {

    /** Shop-local same-day cutoff. */
    static final LocalTime SAME_DAY_CUTOFF = LocalTime.of(14, 0);

    /** What we promise the customer at checkout. */
    public record Promise(LocalDate deliveryDate, boolean sameDay) {}

    private DispatchScheduler() {}

    public static Promise promise(Order order, ZoneId shopZone) {
        LocalDateTime placedLocal = LocalDateTime.ofInstant(order.placedAt(), ZoneOffset.UTC);
        boolean sameDay = placedLocal.toLocalTime().isBefore(SAME_DAY_CUTOFF);
        LocalDate date = sameDay
                ? placedLocal.toLocalDate()
                : placedLocal.toLocalDate().plusDays(1);
        return new Promise(date, sameDay);
    }
}
