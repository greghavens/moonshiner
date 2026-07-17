import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/**
 * Refill-planner acceptance tests. All expectations are fixed dates on the
 * 2026 store calendar; store time is America/Chicago, pinned explicitly.
 * Run: java TestMain.java
 */
public final class TestMain {
    private static int passed = 0;
    private static int failed = 0;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    private static void yes(String what, boolean cond) {
        if (!cond) throw new AssertionError(what);
    }

    private static final ZoneId STORE = ZoneId.of("America/Chicago");

    /** The store's observed closures for 2026 (none fall on a Sunday). */
    private static RefillPlanner planner() {
        return new RefillPlanner(Set.of(
                LocalDate.of(2026, 1, 1),    // New Year's Day (Thu)
                LocalDate.of(2026, 5, 25),   // Memorial Day (Mon)
                LocalDate.of(2026, 7, 3),    // Independence Day observed (Fri)
                LocalDate.of(2026, 9, 7),    // Labor Day (Mon)
                LocalDate.of(2026, 11, 26),  // Thanksgiving (Thu)
                LocalDate.of(2026, 12, 25))); // Christmas (Fri)
    }

    public static void main(String[] args) {

        test("weekday_pickup_is_supply_days_out", () -> {
            Prescription rx = new Prescription("RX-88214", "lisinopril 10mg",
                    LocalDate.of(2026, 3, 10), 30);
            eq("pickup", LocalDate.of(2026, 4, 9), planner().nextPickup(rx));
        });

        test("holiday_pickup_moves_to_next_day", () -> {
            Prescription rx = new Prescription("RX-90031", "metformin 500mg",
                    LocalDate.of(2026, 6, 3), 30);
            // due 2026-07-03 (observed holiday, Friday) -> Saturday the 4th
            eq("pickup", LocalDate.of(2026, 7, 4), planner().nextPickup(rx));
        });

        test("sunday_pickup_moves_to_monday", () -> {
            Prescription rx = new Prescription("RX-77120", "atorvastatin 20mg",
                    LocalDate.of(2026, 2, 6), 30);
            // due 2026-03-08 (a Sunday) -> Monday the 9th
            eq("pickup", LocalDate.of(2026, 3, 9), planner().nextPickup(rx));
        });

        test("sunday_roll_onto_labor_day_lands_tuesday", () -> {
            Prescription rx = new Prescription("RX-66502", "levothyroxine 75mcg",
                    LocalDate.of(2026, 8, 7), 30);
            // due 2026-09-06 (Sunday) -> Monday 09-07 is Labor Day -> Tuesday 09-08
            eq("pickup", LocalDate.of(2026, 9, 8), planner().nextPickup(rx));
        });

        test("reminder_goes_out_the_evening_before", () -> {
            Prescription rx = new Prescription("RX-88214", "lisinopril 10mg",
                    LocalDate.of(2026, 3, 10), 30);
            eq("reminder", ZonedDateTime.of(2026, 4, 8, 17, 30, 0, 0, STORE),
                    planner().reminderFor(rx));
        });

        test("reminder_respects_the_sunday_closure", () -> {
            Prescription rx = new Prescription("RX-77120", "atorvastatin 20mg",
                    LocalDate.of(2026, 2, 6), 30);
            // pickup is Monday 03-09, so the call goes out Sunday 03-08 evening
            eq("reminder", ZonedDateTime.of(2026, 3, 8, 17, 30, 0, 0, STORE),
                    planner().reminderFor(rx));
        });

        test("quarter_schedule_without_closures", () -> {
            Prescription rx = new Prescription("RX-88214", "lisinopril 10mg",
                    LocalDate.of(2026, 3, 10), 30);
            eq("schedule", List.of(LocalDate.of(2026, 4, 9), LocalDate.of(2026, 5, 9)),
                    planner().schedule(rx, 2));
        });

        test("quarter_schedule_stays_off_sundays_and_does_not_drift", () -> {
            Prescription rx = new Prescription("RX-77120", "atorvastatin 20mg",
                    LocalDate.of(2026, 2, 6), 30);
            List<LocalDate> dates = planner().schedule(rx, 3);
            eq("schedule", List.of(
                    LocalDate.of(2026, 3, 9),
                    LocalDate.of(2026, 4, 8),
                    LocalDate.of(2026, 5, 8)), dates);
            for (LocalDate d : dates) {
                yes("no pickup on a Sunday: " + d, d.getDayOfWeek() != DayOfWeek.SUNDAY);
            }
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
