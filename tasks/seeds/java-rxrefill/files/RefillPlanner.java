import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * Computes when a refill is ready for pickup and when the reminder call
 * goes out. Store policy:
 *
 *   - a refill is ready daysSupply days after the last fill
 *   - the store is closed every Sunday: Sunday pickups move to Monday
 *   - the store is closed on observed holidays: those move to the next day
 *     (our observed-holiday calendar never lands on a Sunday)
 *   - the reminder robocall goes out at 17:30 store time the evening
 *     before pickup
 */
public final class RefillPlanner {
    /** All store times are America/Chicago no matter where the batch job runs. */
    static final ZoneId STORE_ZONE = ZoneId.of("America/Chicago");

    private final Set<LocalDate> observedHolidays;

    public RefillPlanner(Set<LocalDate> observedHolidays) {
        this.observedHolidays = Set.copyOf(observedHolidays);
    }

    /** The date the patient can pick up their next refill. */
    public LocalDate nextPickup(Prescription rx) {
        LocalDate pickup = rx.lastFill().plusDays(rx.daysSupply());
        if (pickup.getDayOfWeek() == DayOfWeek.SUNDAY) {
            pickup.plusDays(1);                  // closed Sundays: hand out Monday
        }
        if (observedHolidays.contains(pickup)) {
            pickup = pickup.plusDays(1);         // closed holidays: hand out next day
        }
        return pickup;
    }

    /** Reminder call: 17:30 store time on the evening before pickup. */
    public ZonedDateTime reminderFor(Prescription rx) {
        return nextPickup(rx).minusDays(1).atTime(17, 30).atZone(STORE_ZONE);
    }

    /**
     * Pickup dates for the next {@code refills} fills, assuming the patient
     * picks up on the ready date each time (each pickup becomes the next
     * cycle's fill date).
     */
    public List<LocalDate> schedule(Prescription rx, int refills) {
        List<LocalDate> dates = new ArrayList<>();
        Prescription current = rx;
        for (int i = 0; i < refills; i++) {
            LocalDate pickup = nextPickup(current);
            dates.add(pickup);
            current = new Prescription(current.rxNumber(), current.drug(), pickup, current.daysSupply());
        }
        return dates;
    }
}
