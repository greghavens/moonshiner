import java.util.List;

import cannery.batch.Batch;
import cannery.batch.CookLog;
import cannery.batch.Grader;
import cannery.report.YieldReport;

public class TestMain {
    private static int failures = 0;

    private static void check(boolean ok, String what) {
        if (ok) {
            System.out.println("ok   " + what);
        } else {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    private static void checkEquals(Object expected, Object actual, String what) {
        check(expected.equals(actual), what + " (expected " + expected + ", got " + actual + ")");
    }

    public static void main(String[] args) {
        Grader grader = new Grader();
        checkEquals("case", grader.grade(new Batch("B-11", "peach", 11.0)), "11.0kg packs as a case");
        checkEquals("case", grader.grade(new Batch("B-10", "peach", 10.0)), "10.0kg is exactly the case floor");
        checkEquals("flat", grader.grade(new Batch("B-05", "peach", 5.0)), "5.0kg is exactly the flat floor");
        checkEquals("sample", grader.grade(new Batch("B-45", "peach", 4.5)), "4.5kg goes to sample jars");
        checkEquals("flat", grader.grade(new Batch("B-07", "peach", 7.0)), "7.0kg packs as a flat");

        CookLog log = new CookLog();
        log.record("B-11", 35);
        log.record("B-45", 20);
        checkEquals(List.of("B-11:35m", "B-45:20m"), log.entries(), "cook log keeps kettle order");
        checkEquals(55, log.totalMinutes(), "total cook minutes across the shift");

        boolean rejected = false;
        try {
            log.record("B-99", 0);
        } catch (IllegalArgumentException e) {
            rejected = true;
        }
        check(rejected, "zero-minute cook cycle is rejected");
        checkEquals(2, log.entries().size(), "rejected cycle was not logged");

        YieldReport report = new YieldReport();
        List<String> lines = report.lines(List.of(
                new Batch("B-11", "peach", 11.0),
                new Batch("B-45", "apricot", 4.5)));
        checkEquals(3, lines.size(), "two batch lines plus the total line");
        checkEquals("B-11 | PEACH | 11.0kg -> case", lines.get(0), "first report line");
        checkEquals("B-45 | APRICOT | 4.5kg -> sample", lines.get(1), "second report line");
        checkEquals("TOTAL 15.5kg over 2 batches", lines.get(2), "total line sums the shift");

        checkEquals("TOTAL 0.0kg over 0 batches", report.lines(List.of()).get(0),
                "empty shift still reports a total line");

        if (failures > 0) {
            System.out.println(failures + " check(s) failing");
            System.exit(1);
        }
        System.out.println("all checks passed");
    }
}
