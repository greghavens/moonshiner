import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Acceptance contract for the statement generator.
 *
 * Correctness first: the statement layout is pinned byte-for-byte (the
 * archive checksums every file). Then the month-end budget: one full
 * plaza feed must render inside BUDGET_MS on this machine. The timed
 * window covers ONLY StatementReport.generate() — the feed is built
 * beforehand.
 *
 * Run: java TestMain.java
 */
public class TestMain {

    interface Body {
        void run() throws Exception;
    }

    static int passed = 0;
    static int failed = 0;

    static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    static void eq(Object actual, Object expected, String what) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    static void yes(boolean cond, String what) {
        if (!cond) {
            throw new AssertionError(what);
        }
    }

    // ------------------------------------------------------------------
    // The month-end feed, generated deterministically (fixed LCG seed).

    static long lcg(long[] s) {
        s[0] = s[0] * 6364136223846793005L + 1442695040888963407L;
        return s[0] >>> 33;
    }

    static List<Crossing> feed(int crossings, int accounts) {
        long[] s = {0x70117011_beefL};
        List<Crossing> out = new ArrayList<>(crossings);
        for (int i = 0; i < crossings; i++) {
            String account = String.format(Locale.ROOT, "TP-%05d", lcg(s) % accounts);
            String plaza = String.format(Locale.ROOT, "PLZ-%03d", lcg(s) % 40);
            int day = (int) (lcg(s) % 28) + 1;
            long cents = 95 + (lcg(s) % 1800);
            out.add(new Crossing(account, plaza, day, cents));
        }
        return out;
    }

    static String sha256(String text) throws Exception {
        byte[] hash = MessageDigest.getInstance("SHA-256")
                .digest(text.getBytes(StandardCharsets.UTF_8));
        StringBuilder hex = new StringBuilder();
        for (byte b : hash) {
            hex.append(String.format(Locale.ROOT, "%02x", b));
        }
        return hex.toString();
    }

    // Month-end feed shape and its archived fingerprint.
    static final int FEED_CROSSINGS = 180000;
    static final int FEED_ACCOUNTS = 400;
    static final int FEED_REPORT_LENGTH = 4193075;
    static final String FEED_REPORT_SHA =
            "d73ee6667c160b9d5481f771d6d9a6258196eaa0a296d5d643b2701448b39211";
    static final long BUDGET_MS = 2000;

    public static void main(String[] args) throws Exception {

        test("single_crossing_statement", () -> {
            String report = StatementReport.generate(List.of(
                    new Crossing("TP-00007", "PLZ-004", 3, 250)));
            eq(report,
                    "ACCOUNT TP-00007\n"
                            + "  PLZ-004 day 3 $2.50\n"
                            + "SUBTOTAL TP-00007 trips 1 $2.50\n"
                            + "TOTAL $2.50\n", "report");
        });

        test("accounts_render_in_sorted_order", () -> {
            String report = StatementReport.generate(List.of(
                    new Crossing("TP-00220", "PLZ-001", 5, 1200),
                    new Crossing("TP-00104", "PLZ-030", 2, 95)));
            eq(report,
                    "ACCOUNT TP-00104\n"
                            + "  PLZ-030 day 2 $0.95\n"
                            + "SUBTOTAL TP-00104 trips 1 $0.95\n"
                            + "ACCOUNT TP-00220\n"
                            + "  PLZ-001 day 5 $12.00\n"
                            + "SUBTOTAL TP-00220 trips 1 $12.00\n"
                            + "TOTAL $12.95\n", "report");
        });

        test("crossings_keep_feed_order_within_account", () -> {
            String report = StatementReport.generate(List.of(
                    new Crossing("TP-00002", "PLZ-010", 9, 300),
                    new Crossing("TP-00001", "PLZ-020", 1, 100),
                    new Crossing("TP-00002", "PLZ-005", 4, 450),
                    new Crossing("TP-00001", "PLZ-020", 28, 100),
                    new Crossing("TP-00002", "PLZ-010", 2, 250)));
            eq(report,
                    "ACCOUNT TP-00001\n"
                            + "  PLZ-020 day 1 $1.00\n"
                            + "  PLZ-020 day 28 $1.00\n"
                            + "SUBTOTAL TP-00001 trips 2 $2.00\n"
                            + "ACCOUNT TP-00002\n"
                            + "  PLZ-010 day 9 $3.00\n"
                            + "  PLZ-005 day 4 $4.50\n"
                            + "  PLZ-010 day 2 $2.50\n"
                            + "SUBTOTAL TP-00002 trips 3 $10.00\n"
                            + "TOTAL $12.00\n", "report");
        });

        test("cents_format_edge_cases", () -> {
            String report = StatementReport.generate(List.of(
                    new Crossing("TP-00042", "PLZ-001", 1, 5),
                    new Crossing("TP-00042", "PLZ-002", 2, 100),
                    new Crossing("TP-00042", "PLZ-003", 3, 1999),
                    new Crossing("TP-00042", "PLZ-004", 4, 10000)));
            eq(report,
                    "ACCOUNT TP-00042\n"
                            + "  PLZ-001 day 1 $0.05\n"
                            + "  PLZ-002 day 2 $1.00\n"
                            + "  PLZ-003 day 3 $19.99\n"
                            + "  PLZ-004 day 4 $100.00\n"
                            + "SUBTOTAL TP-00042 trips 4 $121.04\n"
                            + "TOTAL $121.04\n", "report");
        });

        test("empty_feed_is_just_the_total", () -> {
            eq(StatementReport.generate(List.of()), "TOTAL $0.00\n", "report");
        });

        test("same_feed_same_bytes", () -> {
            List<Crossing> smallFeed = feed(500, 12);
            String first = StatementReport.generate(smallFeed);
            String second = StatementReport.generate(smallFeed);
            eq(second, first, "two runs over one feed");
            eq(first.length(), 12240, "report length");
            eq(sha256(first),
                    "d2c39d74fb383cdec1218b9a291916ff94c4336c430a1057cefe44a65472d67e",
                    "archived checksum");
        });

        test("month_end_feed_renders_inside_budget", () -> {
            List<Crossing> monthEnd = feed(FEED_CROSSINGS, FEED_ACCOUNTS);
            long start = System.nanoTime();
            String report = StatementReport.generate(monthEnd);
            long elapsedMs = (System.nanoTime() - start) / 1_000_000;
            System.out.println("  [timing] generate() took " + elapsedMs
                    + " ms (budget " + BUDGET_MS + " ms)");
            eq(report.length(), FEED_REPORT_LENGTH, "report length");
            eq(sha256(report), FEED_REPORT_SHA, "archived checksum");
            yes(elapsedMs <= BUDGET_MS,
                    "month-end feed took " + elapsedMs + " ms, budget is " + BUDGET_MS + " ms");
        });

        System.out.println();
        System.out.println("TOTAL: " + passed + " passed, " + failed + " failed");
        System.exit(failed > 0 ? 1 : 0);
    }
}
