import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Full acceptance contract for the typed meter-feed reader:
 * the shipped FixedWidthReader regression suite (RegressionTests) plus the
 * new layout-DSL / converter / error-collection feature.
 *
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

    private static IllegalArgumentException reject(String what, Runnable r) {
        try {
            r.run();
        } catch (IllegalArgumentException e) {
            return e;
        }
        throw new AssertionError(what + ": expected IllegalArgumentException");
    }

    private static final String MR7_SPEC =
            "meter_id:0:10:text, read_date:10:8:date(yyyyMMdd), reading:18:7:int, "
            + "avg_kw:25:5:dec(2), status:30:1:code(A=ACTUAL|E=ESTIMATED|F=FAILED)";

    // 31-char MR-7 records. Offsets: meter_id [0,10) read_date [10,18)
    // reading [18,25) avg_kw [25,30) status [30,31).
    private static final String GOOD_4417 = "MTR000441720260630  5734200415A";
    private static final String GOOD_9021 = "MTR000902120260630001277600088E";
    private static final String GOOD_1150 = "MTR000115020260630000004100012F";

    private static ReadResult readMr7(String content) {
        return new LayoutReader(RecordLayout.parse(MR7_SPEC)).read(content);
    }

    public static void main(String[] args) {
        int regressionFailures = RegressionTests.runAll();

        test("layout_parse_exposes_names_and_width", () -> {
            RecordLayout layout = RecordLayout.parse(MR7_SPEC);
            eq("names", "[meter_id, read_date, reading, avg_kw, status]", layout.names().toString());
            eq("width", 31, layout.width());
        });

        test("layout_rejects_malformed_column_entry", () -> {
            IllegalArgumentException e = reject("two-part entry",
                    () -> RecordLayout.parse("meter_id:0:10:text, kwh:18"));
            eq("message", "bad column spec 'kwh:18' (want name:start:len:type)", e.getMessage());
        });

        test("layout_rejects_unknown_type", () -> {
            IllegalArgumentException e = reject("float column",
                    () -> RecordLayout.parse("kwh:18:7:float"));
            eq("message", "unknown type 'float' in column 'kwh'", e.getMessage());
        });

        test("layout_rejects_duplicate_column_names", () -> {
            IllegalArgumentException e = reject("duplicate kwh",
                    () -> RecordLayout.parse("kwh:0:7:int, kwh:7:7:int"));
            eq("message", "duplicate column 'kwh'", e.getMessage());
        });

        test("layout_rejects_bad_spans", () -> {
            IllegalArgumentException negStart = reject("negative start",
                    () -> RecordLayout.parse("kwh:-1:7:int"));
            eq("negative start message", "bad span in column 'kwh'", negStart.getMessage());
            IllegalArgumentException zeroLen = reject("zero length",
                    () -> RecordLayout.parse("kwh:5:0:int"));
            eq("zero length message", "bad span in column 'kwh'", zeroLen.getMessage());
            IllegalArgumentException notInt = reject("non-numeric start",
                    () -> RecordLayout.parse("kwh:x:7:int"));
            eq("non-numeric message", "bad span in column 'kwh'", notInt.getMessage());
        });

        test("layout_rejects_empty_spec", () -> {
            eq("empty string", "empty layout",
                    reject("empty", () -> RecordLayout.parse("")).getMessage());
            eq("only separators", "empty layout",
                    reject("separators", () -> RecordLayout.parse("  ,  ")).getMessage());
        });

        test("typed_read_converts_every_column", () -> {
            ReadResult r = readMr7(GOOD_4417);
            yes("clean", r.clean());
            eq("no errors", 0, r.errors().size());
            eq("one row", 1, r.rows().size());
            Map<String, Object> row = r.rows().get(0);
            eq("key order", "[meter_id, read_date, reading, avg_kw, status]", row.keySet().toString());
            eq("meter_id", "MTR0004417", row.get("meter_id"));
            eq("read_date", LocalDate.of(2026, 6, 30), row.get("read_date"));
            eq("reading", 57342L, row.get("reading"));
            eq("avg_kw", new BigDecimal("4.15"), row.get("avg_kw"));
            eq("status", "ACTUAL", row.get("status"));
        });

        test("int_cells_allow_leading_zeros_and_signs", () -> {
            ReadResult r = new LayoutReader(RecordLayout.parse("delta:0:7:int"))
                    .read("0000042\n  -1204\n    -07");
            yes("clean", r.clean());
            eq("rows", 3, r.rows().size());
            eq("padded positive", 42L, r.rows().get(0).get("delta"));
            eq("negative", -1204L, r.rows().get(1).get("delta"));
            eq("padded negative", -7L, r.rows().get(2).get("delta"));
        });

        test("dec_applies_the_implied_scale_exactly", () -> {
            ReadResult cents = new LayoutReader(RecordLayout.parse("amount:0:7:dec(2)"))
                    .read("0000000\n0012050\n-000205");
            yes("clean", cents.clean());
            eq("zero keeps scale 2", new BigDecimal("0.00"), cents.rows().get(0).get("amount"));
            eq("12050 over 100", new BigDecimal("120.50"), cents.rows().get(1).get("amount"));
            eq("negative", new BigDecimal("-2.05"), cents.rows().get(2).get("amount"));
            ReadResult tenths = new LayoutReader(RecordLayout.parse("kwh:0:7:dec(1)"))
                    .read("0000345");
            eq("dec(1)", new BigDecimal("34.5"), tenths.rows().get(0).get("kwh"));
        });

        test("date_uses_the_declared_pattern", () -> {
            ReadResult compact = new LayoutReader(RecordLayout.parse("d:0:8:date(yyyyMMdd)"))
                    .read("20260215");
            eq("compact pattern", LocalDate.of(2026, 2, 15), compact.rows().get(0).get("d"));
            ReadResult dashed = new LayoutReader(RecordLayout.parse("d:0:10:date(yyyy-MM-dd)"))
                    .read("2026-06-30");
            eq("dashed pattern", LocalDate.of(2026, 6, 30), dashed.rows().get(0).get("d"));
        });

        test("conversion_failures_are_collected_not_thrown", () -> {
            String badReading = "MTR00044172026063000X734200415A";
            ReadResult r = readMr7(badReading);
            yes("not clean", !r.clean());
            eq("no rows survive", 0, r.rows().size());
            eq("errors", List.of(new RowError(1, "reading", "not an integer: '00X7342'")), r.errors());
        });

        test("every_bad_column_in_a_row_is_reported_in_spec_order", () -> {
            String threeBad = "MTR00044172026130100A734200415X";
            ReadResult r = readMr7(threeBad);
            eq("errors", List.of(
                    new RowError(1, "read_date", "bad date: '20261301'"),
                    new RowError(1, "reading", "not an integer: '00A7342'"),
                    new RowError(1, "status", "unknown code: 'X'")), r.errors());
            eq("no rows", 0, r.rows().size());
        });

        test("short_lines_report_each_overrunning_column", () -> {
            // 20 chars: meter_id and read_date fit, the rest overrun.
            String stub = "MTR0004417" + "20261399";
            ReadResult r = readMr7(stub + "00");
            eq("errors", List.of(
                    new RowError(1, "read_date", "bad date: '20261399'"),
                    new RowError(1, "reading", "past end of line (20 chars)"),
                    new RowError(1, "avg_kw", "past end of line (20 chars)"),
                    new RowError(1, "status", "past end of line (20 chars)")), r.errors());
        });

        test("error_lines_are_physical_line_numbers", () -> {
            String file = GOOD_4417 + "\n"
                    + "MTR000902120260630001277600088Z\n"
                    + "\n"
                    + "MTR00011502026063000000J100012F\n";
            ReadResult r = readMr7(file);
            eq("rows", 1, r.rows().size());
            eq("surviving meter", "MTR0004417", r.rows().get(0).get("meter_id"));
            eq("errors", List.of(
                    new RowError(2, "status", "unknown code: 'Z'"),
                    new RowError(4, "reading", "not an integer: '00000J1'")), r.errors());
        });

        test("batch_with_mixed_rows_keeps_good_rows_in_order", () -> {
            String file = GOOD_4417 + "\n"
                    + GOOD_9021 + "\n"
                    + "MTR000733320269930001277600088E\n"
                    + GOOD_1150 + "\n"
                    + "MTR000801120260630001277600088Q\n";
            ReadResult r = readMr7(file);
            yes("not clean", !r.clean());
            eq("rows", 3, r.rows().size());
            eq("row 0", "MTR0004417", r.rows().get(0).get("meter_id"));
            eq("row 1", "MTR0009021", r.rows().get(1).get("meter_id"));
            eq("row 2", "MTR0001150", r.rows().get(2).get("meter_id"));
            eq("error count", 2, r.errors().size());
            eq("bad date line", 3, r.errors().get(0).line());
            eq("bad code line", 5, r.errors().get(1).line());
        });

        test("fully_clean_batch_reports_clean", () -> {
            ReadResult r = readMr7(GOOD_4417 + "\n" + GOOD_9021 + "\n" + GOOD_1150 + "\n");
            yes("clean", r.clean());
            eq("rows", 3, r.rows().size());
            eq("errors", List.of(), r.errors());
            eq("row 2 status", "FAILED", r.rows().get(2).get("status"));
            eq("row 2 avg_kw", new BigDecimal("0.12"), r.rows().get(2).get("avg_kw"));
            eq("row 2 reading", 41L, r.rows().get(2).get("reading"));
        });

        System.out.println("feature: " + passed + " passed, " + failed + " failed");
        int total = regressionFailures + failed;
        System.out.println("TOTAL failures: " + total);
        System.exit(total > 0 ? 1 : 0);
    }
}
