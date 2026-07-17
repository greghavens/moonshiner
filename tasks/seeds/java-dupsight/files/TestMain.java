import java.time.LocalDate;
import java.util.List;
import java.util.Objects;

/**
 * Weekly-roundup acceptance tests for the sighting log.
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

    private static Sighting pileated(String spotter) {
        return new Sighting("PIWO", "Cedar Bog boardwalk", LocalDate.of(2026, 4, 18), spotter);
    }

    public static void main(String[] args) {

        test("matching_reports_compare_equal_directly", () -> {
            yes("same sighting, different spotter", pileated("June H.").equals(pileated("Ravi P.")));
            yes("different species is a different sighting", !pileated("June H.").equals(
                    new Sighting("NOCA", "Cedar Bog boardwalk", LocalDate.of(2026, 4, 18), "June H.")));
            yes("different site is a different sighting", !pileated("June H.").equals(
                    new Sighting("PIWO", "Mill Creek overlook", LocalDate.of(2026, 4, 18), "June H.")));
            yes("different date is a different sighting", !pileated("June H.").equals(
                    new Sighting("PIWO", "Cedar Bog boardwalk", LocalDate.of(2026, 4, 19), "June H.")));
        });

        test("weekly_merge_collapses_duplicate_reports", () -> {
            String[] codes = {"PIWO", "NOCA", "BAEA", "AMCR", "RTHA", "CANG",
                              "BCCH", "DOWO", "WBNU", "TUTI", "EABL", "AMGO"};
            SightingLog log = new SightingLog();
            for (int i = 0; i < codes.length; i++) {
                LocalDate day = LocalDate.of(2026, 4, 13 + (i % 7));
                String site = "Route " + (1 + (i % 4));
                log.record(new Sighting(codes[i], site, day, "June H."));
                log.record(new Sighting(codes[i], site, day, "Ravi P."));
            }
            eq("totalReports", 24, log.totalReports());
            eq("uniqueCount", 12, log.uniqueCount());
        });

        test("already_recorded_answers_yes_for_fresh_copies", () -> {
            Sighting[] week = {
                new Sighting("PIWO", "Cedar Bog boardwalk", LocalDate.of(2026, 4, 14), "June H."),
                new Sighting("NOCA", "Mill Creek overlook", LocalDate.of(2026, 4, 14), "Ravi P."),
                new Sighting("BAEA", "Quarry ponds", LocalDate.of(2026, 4, 15), "Marisol T."),
                new Sighting("RTHA", "Route 3", LocalDate.of(2026, 4, 16), "June H."),
                new Sighting("CANG", "Quarry ponds", LocalDate.of(2026, 4, 17), "Ravi P."),
            };
            SightingLog log = new SightingLog();
            for (Sighting s : week) {
                log.record(s);
            }
            int recognized = 0;
            for (Sighting s : week) {
                Sighting probe = new Sighting(s.speciesCode(), s.site(), s.date(), "front-desk check");
                if (log.alreadyRecorded(probe)) recognized++;
            }
            eq("recognized on re-check", 5, recognized);
            yes("unrelated sighting is not recorded", !log.alreadyRecorded(
                    new Sighting("SNOW", "Route 9", LocalDate.of(2026, 4, 18), "June H.")));
        });

        test("second_report_of_same_sighting_is_not_new", () -> {
            SightingLog log = new SightingLog();
            yes("first report is new", log.record(pileated("June H.")));
            yes("second report only confirms it", !log.record(pileated("Ravi P.")));
            eq("uniqueCount", 1, log.uniqueCount());
            eq("totalReports", 2, log.totalReports());
        });

        test("confirmations_tally_counts_every_spotter", () -> {
            SightingLog log = new SightingLog();
            log.record(pileated("June H."));
            log.record(pileated("Ravi P."));
            log.record(pileated("Marisol T."));
            eq("confirmations", 3, log.confirmations(pileated("newsletter editor")));
            eq("unknown sighting has no confirmations", 0, log.confirmations(
                    new Sighting("SNOW", "Quarry ponds", LocalDate.of(2026, 4, 18), "June H.")));
        });

        test("distinct_sightings_all_kept_and_indexed", () -> {
            SightingLog log = new SightingLog();
            log.record(new Sighting("PIWO", "Cedar Bog boardwalk", LocalDate.of(2026, 4, 14), "June H."));
            log.record(new Sighting("PIWO", "Mill Creek overlook", LocalDate.of(2026, 4, 14), "June H."));
            log.record(new Sighting("NOCA", "Cedar Bog boardwalk", LocalDate.of(2026, 4, 15), "Ravi P."));
            log.record(new Sighting("BAEA", "Quarry ponds", LocalDate.of(2026, 4, 16), "Marisol T."));
            log.record(new Sighting("AMCR", "Route 3", LocalDate.of(2026, 4, 16), "June H."));
            log.record(new Sighting("CANG", "Quarry ponds", LocalDate.of(2026, 4, 17), "Ravi P."));
            eq("uniqueCount", 6, log.uniqueCount());
            eq("speciesIndex", List.of("AMCR", "BAEA", "CANG", "NOCA", "PIWO"), log.speciesIndex());
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
