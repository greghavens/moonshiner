import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/** Behavior suite for the depot board module. Do not modify. */
public class TestMain {
    static int passed = 0;

    static void check(boolean cond, String what) {
        if (!cond) {
            System.out.println("FAIL: " + what);
            System.exit(1);
        }
        passed++;
    }

    static void checkEq(Object expected, Object actual, String what) {
        check(expected.equals(actual), what + " — expected " + expected + ", got " + actual);
    }

    public static void main(String[] args) {
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        Locale.setDefault(Locale.ROOT);

        // service-day calendar, June 2026
        checkEq("WEEKDAY", DepotBoard.serviceKind(2026, 6, 10), "Wednesday runs the weekday pattern");
        checkEq("SATURDAY", DepotBoard.serviceKind(2026, 6, 13), "Saturday pattern");
        checkEq("SUNDAY", DepotBoard.serviceKind(2026, 6, 14), "Sunday pattern");
        checkEq("WEEKDAY", DepotBoard.serviceKind(2026, 6, 8), "Monday runs the weekday pattern");
        checkEq("WEEKDAY", DepotBoard.serviceKind(2026, 6, 12), "Friday runs the weekday pattern");

        // clock rendering, including boards that roll past midnight
        checkEq("00:00", DepotBoard.clock(0), "midnight");
        checkEq("04:45", DepotBoard.clock(285), "first weekday tram");
        checkEq("23:59", DepotBoard.clock(1439), "last minute of the day");
        checkEq("00:10", DepotBoard.clock(1450), "rolls past midnight");
        checkEq("01:05", DepotBoard.clock(2945), "rolls more than a full day");

        // departure tables
        checkEq(List.of(285, 310, 335, 360, 385, 410),
                DepotBoard.departureMinutes("WEEKDAY"), "weekday departures");
        checkEq(List.of(360, 400, 440, 480, 520),
                DepotBoard.departureMinutes("SATURDAY"), "saturday departures");
        checkEq(List.of(1380, 1415, 1450, 1485),
                DepotBoard.departureMinutes("SUNDAY"), "sunday departures");
        boolean threw = false;
        try {
            DepotBoard.departureMinutes("FOOTY_NIGHT");
        } catch (IllegalArgumentException e) {
            threw = true;
            checkEq("no timetable for FOOTY_NIGHT", e.getMessage(), "unknown pattern message");
        }
        check(threw, "unknown pattern must be rejected");

        // full kiosk boards
        checkEq(List.of("Riverside / SUNDAY",
                        "23:00 Riverside", "23:35 Riverside", "00:10 Riverside", "00:45 Riverside"),
                DepotBoard.renderBoard(2026, 6, 14, "Riverside"), "sunday board rolls past midnight");
        checkEq(List.of("Crosstown / WEEKDAY",
                        "04:45 Crosstown", "05:10 Crosstown", "05:35 Crosstown",
                        "06:00 Crosstown", "06:25 Crosstown", "06:50 Crosstown"),
                DepotBoard.renderBoard(2026, 6, 10, "Crosstown"), "weekday board");

        // crew tags carry the printing thread's id
        checkEq("run-7/crew-" + Thread.currentThread().threadId(),
                DepotBoard.crewTag("7"), "crew tag for this thread");
        checkEq("run-12A/crew-" + Thread.currentThread().threadId(),
                DepotBoard.crewTag("12A"), "crew tag keeps the run name");

        System.out.println("all " + passed + " checks PASS");
    }
}
