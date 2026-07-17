import java.util.Objects;

/**
 * Reservation-book acceptance tests, replaying the same booking workflow
 * against the studio seating chart (seats 1-80) and the main hall chart
 * (seats 101-412).
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

    public static void main(String[] args) {

        test("studio_book_dedupes_and_finds_holds", () -> {
            BoxOffice studio = new BoxOffice("Cabaret Night (studio)");
            yes("seat 12 accepted", studio.reserve("Lena Voss", 12));
            yes("seat 44 accepted", studio.reserve("Marc Abitbol", 44));
            yes("seat 80 accepted", studio.reserve("Priya Nair", 80));
            yes("seat 44 double-booking refused", !studio.reserve("Walk-up", 44));
            eq("holder of seat 44", "Marc Abitbol", studio.patronFor(44));
            yes("seat 12 shows taken", studio.isTaken(12));
            yes("release of seat 12", studio.release(12));
            yes("seat 12 free after release", !studio.isTaken(12));
            eq("holds in the book", 2, studio.holds());
        });

        test("main_hall_refuses_double_booking", () -> {
            BoxOffice hall = new BoxOffice("Winter Gala (main hall)");
            yes("seat 245 accepted", hall.reserve("Imani Okafor", 245));
            yes("seat 245 second sale refused", !hall.reserve("Theo Brandt", 245));
            eq("holds in the book", 1, hall.holds());
            eq("holder of seat 245", "Imani Okafor", hall.patronFor(245));
        });

        test("main_hall_finds_patron_for_sold_seat", () -> {
            BoxOffice hall = new BoxOffice("Winter Gala (main hall)");
            yes("seat 310 accepted", hall.reserve("Ada Lindqvist", 310));
            yes("seat 310 shows taken", hall.isTaken(310));
            eq("holder of seat 310", "Ada Lindqvist", hall.patronFor(310));
            eq("seat 311 is free", null, hall.patronFor(311));
        });

        test("main_hall_refund_releases_hold", () -> {
            BoxOffice hall = new BoxOffice("Winter Gala (main hall)");
            yes("seat 199 accepted", hall.reserve("Sofia Marek", 199));
            yes("refund finds the hold", hall.release(199));
            yes("seat 199 free after refund", !hall.isTaken(199));
            eq("book is empty", 0, hall.holds());
            yes("seat 199 sellable again", hall.reserve("Next Patron", 199));
        });

        test("row_g_sweep_reports_every_sold_seat", () -> {
            BoxOffice hall = new BoxOffice("Winter Gala (main hall)");
            for (int seat = 120; seat <= 140; seat++) {
                yes("seat " + seat + " accepted", hall.reserve("Subscriber " + seat, seat));
            }
            int reportedTaken = 0;
            for (int seat = 120; seat <= 140; seat++) {
                if (hall.isTaken(seat)) reportedTaken++;
            }
            eq("sold seats reported by the row sweep", 21, reportedTaken);
        });

        test("general_admission_holds_never_block_seats", () -> {
            BoxOffice hall = new BoxOffice("Standing Room Special");
            yes("first GA hold accepted", hall.reserve("GA Patron One", null));
            yes("second GA hold accepted", hall.reserve("GA Patron Two", null));
            yes("GA is not a seat", !hall.isTaken(null));
            yes("GA cannot be released by seat", !hall.release(null));
            eq("holds in the book", 2, hall.holds());
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
