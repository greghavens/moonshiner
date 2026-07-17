import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Pins the shipped FixedWidthReader behavior. The nightly import and two
 * other batch jobs depend on exactly this; it must keep passing unchanged.
 *
 * Standalone run: java RegressionTests.java
 * (TestMain also runs this suite as part of the full contract.)
 */
public final class RegressionTests {
    private static int passed = 0;
    private static int failed = 0;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS reg:" + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL reg:" + name + ": " + t);
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

    private static FixedWidthReader mr7Reader() {
        return new FixedWidthReader()
                .field("meter_id", 0, 10)
                .field("read_date", 10, 8)
                .field("reading", 18, 7)
                .field("avg_kw", 25, 5)
                .field("status", 30, 1);
    }

    /** Runs the suite, printing one line per test; returns the failure count. */
    public static int runAll() {
        passed = 0;
        failed = 0;

        test("extracts_fields_by_offset_and_trims", () -> {
            List<Map<String, String>> rows = mr7Reader().read("MTR000441720260630  5734200415A");
            eq("row count", 1, rows.size());
            Map<String, String> r = rows.get(0);
            eq("meter_id", "MTR0004417", r.get("meter_id"));
            eq("read_date", "20260630", r.get("read_date"));
            eq("reading (trimmed)", "57342", r.get("reading"));
            eq("avg_kw", "00415", r.get("avg_kw"));
            eq("status", "A", r.get("status"));
        });

        test("keeps_field_registration_order_in_each_row", () -> {
            Map<String, String> r = mr7Reader().read("MTR000441720260630  5734200415A").get(0);
            eq("key order", "[meter_id, read_date, reading, avg_kw, status]", r.keySet().toString());
        });

        test("reads_every_nonblank_line_and_skips_blanks", () -> {
            String file = "MTR000441720260630  5734200415A\n"
                    + "\n"
                    + "MTR000902120260630001277600088E\n";
            List<Map<String, String>> rows = mr7Reader().read(file);
            eq("row count", 2, rows.size());
            eq("second meter", "MTR0009021", rows.get(1).get("meter_id"));
        });

        test("tolerates_dos_line_endings", () -> {
            String file = "MTR000441720260630  5734200415A\r\nMTR000902120260630001277600088E\r\n";
            List<Map<String, String>> rows = mr7Reader().read(file);
            eq("row count", 2, rows.size());
            eq("status of row 2", "E", rows.get(1).get("status"));
        });

        test("short_line_aborts_with_physical_line_number", () -> {
            String file = "MTR0004417202606300057342 00415A\nMTR000902120260630\n";
            try {
                mr7Reader().read(file);
                throw new AssertionError("expected IllegalArgumentException");
            } catch (IllegalArgumentException e) {
                eq("message", "line 2 is 18 chars, field 'reading' needs 25", e.getMessage());
            }
        });

        test("empty_content_yields_no_rows", () -> {
            eq("rows for empty string", 0, mr7Reader().read("").size());
            eq("rows for null", 0, mr7Reader().read(null).size());
        });

        test("duplicate_field_names_are_rejected", () -> {
            try {
                new FixedWidthReader().field("reading", 0, 5).field("reading", 5, 5);
                throw new AssertionError("expected IllegalArgumentException");
            } catch (IllegalArgumentException e) {
                eq("message", "duplicate field 'reading'", e.getMessage());
            }
        });

        test("reading_with_no_fields_registered_is_an_error", () -> {
            try {
                new FixedWidthReader().read("anything");
                throw new AssertionError("expected IllegalStateException");
            } catch (IllegalStateException e) {
                eq("message", "no fields registered", e.getMessage());
            }
        });

        test("bad_spans_are_rejected_at_registration", () -> {
            try {
                new FixedWidthReader().field("x", -1, 4);
                throw new AssertionError("expected IllegalArgumentException for negative start");
            } catch (IllegalArgumentException e) {
                eq("message", "bad span for field 'x'", e.getMessage());
            }
            try {
                new FixedWidthReader().field("x", 0, 0);
                throw new AssertionError("expected IllegalArgumentException for zero length");
            } catch (IllegalArgumentException e) {
                eq("message", "bad span for field 'x'", e.getMessage());
            }
        });

        System.out.println("regression: " + passed + " passed, " + failed + " failed");
        return failed;
    }

    public static void main(String[] args) {
        int f = runAll();
        System.exit(f > 0 ? 1 : 0);
    }
}
