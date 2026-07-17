import java.time.LocalDate;
import java.util.List;
import java.util.Locale;
import java.util.Objects;

/**
 * Export-contract tests for the end-of-day sales CSV.
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

    private static List<TicketSale> saturdaySales() {
        LocalDate day = LocalDate.of(2026, 1, 17);
        return List.of(
                new TicketSale(day, "ADULT-FULL", 2, 4250),
                new TicketSale(day, "CHILD-HALF", 3, 2125),
                new TicketSale(day, "SENIOR", 1, 950),
                new TicketSale(day, "COMP", 4, 0));
    }

    private static String[] exportLines() {
        return new SalesCsvExporter().export(saturdaySales()).split("\n", -1);
    }

    public static void main(String[] args) {
        // The nightly batch also runs on the accounting workstation, which is
        // imaged with EU regional settings. The export contract demands the
        // same bytes from every machine, so the suite runs under that image.
        Locale.setDefault(Locale.GERMANY);

        test("header_line_matches_the_import_spec", () -> {
            eq("header", "date,ticket_type,qty,unit_price,total", exportLines()[0]);
        });

        test("one_line_per_sale_plus_header_and_total", () -> {
            String csv = new SalesCsvExporter().export(saturdaySales());
            eq("line count", 6, csv.split("\n", -1).length);
            yes("no trailing newline", !csv.endsWith("\n"));
        });

        test("every_row_has_exactly_five_fields", () -> {
            String[] lines = exportLines();
            for (int i = 1; i < lines.length; i++) {
                eq("fields in row " + i + " <" + lines[i] + ">", 5, lines[i].split(",", -1).length);
            }
        });

        test("price_columns_use_point_decimals", () -> {
            String[] lines = exportLines();
            String[] expectedUnit = {"42.50", "21.25", "9.50", "0.00"};
            for (int i = 0; i < expectedUnit.length; i++) {
                String[] fields = lines[i + 1].split(",", -1);
                eq("unit_price in row " + (i + 1), expectedUnit[i], fields[3]);
                yes("unit_price matches N.NN in row " + (i + 1), fields[3].matches("\\d+\\.\\d{2}"));
            }
        });

        test("totals_column_reconciles_against_the_register", () -> {
            String[] lines = exportLines();
            double sum = 0;
            for (int i = 1; i <= 4; i++) {
                String[] fields = lines[i].split(",", -1);
                sum += Double.parseDouble(fields[4]);
            }
            eq("sum of line totals", 158.25, sum);
        });

        test("qty_column_survives_the_export", () -> {
            String[] lines = exportLines();
            String[] expectedQty = {"2", "3", "1", "4"};
            for (int i = 0; i < expectedQty.length; i++) {
                eq("qty in row " + (i + 1), expectedQty[i], lines[i + 1].split(",", -1)[2]);
            }
            eq("qty in TOTAL row", "10", lines[5].split(",", -1)[2]);
        });

        test("total_row_is_exact", () -> {
            eq("TOTAL row", "TOTAL,,10,,158.25", exportLines()[5]);
        });

        test("adult_row_is_exact", () -> {
            eq("first sale row", "2026-01-17,ADULT-FULL,2,42.50,85.00", exportLines()[1]);
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
