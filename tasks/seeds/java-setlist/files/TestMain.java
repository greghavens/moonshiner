import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Compliance-sweep acceptance tests using the fixtures from the incident
 * reports (board-op tickets 4417 and 4423).
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

    private static List<String> titles(List<Track> setlist) {
        List<String> out = new ArrayList<>();
        for (Track t : setlist) {
            out.add(t.title());
        }
        return out;
    }

    public static void main(String[] args) {

        test("clean_block_sails_through_untouched", () -> {
            List<Track> block = new ArrayList<>(List.of(
                    new Track("Harbor Lights", "The Meridian", 210, false),
                    new Track("Paper Kites", "Lena Duval", 245, false),
                    new Track("North Platform", "Static Fern", 232, false),
                    new Track("Blue Hour", "The Meridian", 180, false),
                    new Track("Silver Route", "Kite Parade", 299, false),
                    new Track("Last Ferry", "Lena Duval", 285, false)));
            ComplianceSweep sweep = new ComplianceSweep(300, false);
            eq("pulled titles", List.of(), sweep.enforce(block));
            eq("running order", List.of("Harbor Lights", "Paper Kites", "North Platform",
                    "Blue Hour", "Silver Route", "Last Ferry"), titles(block));
        });

        test("overlong_album_cut_is_pulled_mid_block", () -> {
            List<Track> block = new ArrayList<>(List.of(
                    new Track("Harbor Lights", "The Meridian", 210, false),
                    new Track("Paper Kites", "Lena Duval", 245, false),
                    new Track("Cathedral (album version)", "Static Fern", 407, false),
                    new Track("Blue Hour", "The Meridian", 180, false),
                    new Track("Silver Route", "Kite Parade", 299, false),
                    new Track("Last Ferry", "Lena Duval", 285, false),
                    new Track("Closing Bell", "Static Fern", 240, false)));
            ComplianceSweep sweep = new ComplianceSweep(300, true);
            eq("pulled titles", List.of("Cathedral (album version)"), sweep.enforce(block));
            eq("running order", List.of("Harbor Lights", "Paper Kites", "Blue Hour",
                    "Silver Route", "Last Ferry", "Closing Bell"), titles(block));
        });

        test("daytime_sweep_pulls_every_explicit_track", () -> {
            List<Track> block = new ArrayList<>(List.of(
                    new Track("Morning Static", "Kite Parade", 200, false),
                    new Track("Backroom Talk", "Vera Quinn", 190, true),
                    new Track("Glasshouse", "The Meridian", 230, false),
                    new Track("Old Coats", "Lena Duval", 210, false),
                    new Track("Knife Drawer", "Vera Quinn", 250, true),
                    new Track("Tidal Road", "Static Fern", 230, false),
                    new Track("Ninth Street", "Kite Parade", 210, false),
                    new Track("Evening Static", "The Meridian", 260, false)));
            ComplianceSweep sweep = new ComplianceSweep(480, false);
            eq("pulled titles", List.of("Backroom Talk", "Knife Drawer"), sweep.enforce(block));
            eq("running order", List.of("Morning Static", "Glasshouse", "Old Coats",
                    "Tidal Road", "Ninth Street", "Evening Static"), titles(block));
        });

        test("flagged_pair_closing_the_block_is_fully_pulled", () -> {
            List<Track> block = new ArrayList<>(List.of(
                    new Track("Harbor Lights", "The Meridian", 200, false),
                    new Track("Paper Kites", "Lena Duval", 210, false),
                    new Track("Glasshouse", "The Meridian", 190, false),
                    new Track("Backroom Talk", "Vera Quinn", 240, true),
                    new Track("Knife Drawer", "Vera Quinn", 230, true)));
            ComplianceSweep sweep = new ComplianceSweep(480, false);
            eq("pulled titles", List.of("Backroom Talk", "Knife Drawer"), sweep.enforce(block));
            eq("running order", List.of("Harbor Lights", "Paper Kites", "Glasshouse"),
                    titles(block));
        });

        test("survivors_keep_running_order_around_scattered_pulls", () -> {
            List<Track> block = new ArrayList<>(List.of(
                    new Track("Morning Static", "Kite Parade", 220, false),
                    new Track("Harbor Lights", "The Meridian", 240, false),
                    new Track("Blue Hour", "The Meridian", 180, false),
                    new Track("Cathedral (album version)", "Static Fern", 520, false),
                    new Track("Old Coats", "Lena Duval", 260, false),
                    new Track("Tidal Road", "Static Fern", 210, false),
                    new Track("Backroom Talk", "Vera Quinn", 250, true),
                    new Track("Ninth Street", "Kite Parade", 230, false),
                    new Track("Last Ferry", "Lena Duval", 240, false)));
            ComplianceSweep sweep = new ComplianceSweep(300, false);
            eq("pulled titles", List.of("Cathedral (album version)", "Backroom Talk"),
                    sweep.enforce(block));
            eq("running order", List.of("Morning Static", "Harbor Lights", "Blue Hour",
                    "Old Coats", "Tidal Road", "Ninth Street", "Last Ferry"), titles(block));
        });

        test("safe_harbor_slot_keeps_explicit_and_long_cuts", () -> {
            List<Track> block = new ArrayList<>(List.of(
                    new Track("Backroom Talk", "Vera Quinn", 190, true),
                    new Track("Cathedral (album version)", "Static Fern", 520, false),
                    new Track("Knife Drawer", "Vera Quinn", 250, true)));
            ComplianceSweep sweep = new ComplianceSweep(600, true);
            eq("pulled titles", List.of(), sweep.enforce(block));
            eq("running order", List.of("Backroom Talk", "Cathedral (album version)",
                    "Knife Drawer"), titles(block));
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
