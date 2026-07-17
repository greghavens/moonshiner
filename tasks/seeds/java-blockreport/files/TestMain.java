import java.util.List;

/**
 * Acceptance contract for the nightly overdue artifacts.
 *
 * The warehouse importer parses the .sql file line-by-line and the
 * returns-desk printer needs every slip record exactly 32 characters
 * wide, so both outputs are pinned byte-for-byte.
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

    static final List<OverdueSlips.Loan> TWO_LOANS = List.of(
            new OverdueSlips.Loan("The Left Hand of Darkness", 9, 2.25),
            new OverdueSlips.Loan("Small Engine Repair", 3, 0.75));

    public static void main(String[] args) {
        test("export sql matches the importer contract exactly", () -> {
            String want = String.join("\n",
                    "-- overdue export, generated for as_of=2026-06-01",
                    "INSERT INTO overdue_export (branch, card_no, title, days_late)",
                    "SELECT b.code, l.card_no, i.title, (:as_of - l.due_on)",
                    "FROM loans l",
                    "JOIN items i ON i.id = l.item_id",
                    "JOIN branches b ON b.id = l.branch_id",
                    "WHERE l.returned_on IS NULL AND l.due_on < :as_of",
                    "ORDER BY b.code, l.card_no;",
                    "");
            eq(OverdueSlips.exportSql("2026-06-01"), want, "exportSql");
        });

        test("export sql contains no blank lines for the importer to choke on", () -> {
            String[] lines = OverdueSlips.exportSql("2026-06-01").split("\n", -1);
            for (int i = 0; i < lines.length - 1; i++) {
                yes(!lines[i].isEmpty(), "line " + (i + 1) + " of the export is blank");
            }
        });

        test("every slip record is exactly 32 characters wide", () -> {
            String slip = OverdueSlips.slip("R-2214", TWO_LOANS);
            for (String line : slip.split("\n")) {
                eq(line.length(), 32, "width of record '" + line + "'");
            }
        });

        test("the two-loan slip renders exactly", () -> {
            String want =
                    "RIVERBEND BRANCH LIBRARY        \n" +
                    "OVERDUE ITEMS - KEEP SLIP       \n" +
                    "--------------------------------\n" +
                    "CARD R-2214                     \n" +
                    "The Left Hand of Darkn   9 $2.25\n" +
                    "Small Engine Repair      3 $0.75\n" +
                    "--------------------------------\n" +
                    "TOTAL DUE                  $3.00\n";
            eq(OverdueSlips.slip("R-2214", TWO_LOANS), want, "slip");
        });

        test("a card with nothing outstanding still gets a complete slip", () -> {
            String want =
                    "RIVERBEND BRANCH LIBRARY        \n" +
                    "OVERDUE ITEMS - KEEP SLIP       \n" +
                    "--------------------------------\n" +
                    "CARD R-0007                     \n" +
                    "--------------------------------\n" +
                    "TOTAL DUE                  $0.00\n";
            eq(OverdueSlips.slip("R-0007", List.of()), want, "empty slip");
        });

        test("long titles are clipped to the title column", () -> {
            String slip = OverdueSlips.slip("R-9001", List.of(
                    new OverdueSlips.Loan("Encyclopedia of Practical Home Repair", 14, 4.50)));
            yes(slip.contains("Encyclopedia of Practi  14 $4.50\n"),
                    "clipped row missing; got:\n" + slip);
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) {
            System.exit(1);
        }
    }
}
