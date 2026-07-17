import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.TimeZone;
import java.util.concurrent.CyclicBarrier;

/**
 * Swipe-ingest acceptance tests: single-controller replays, malformed-stamp
 * rejection, and the simultaneous-upload window that reconciliation keeps
 * flagging. Every expected value is precomputed; the upload window is
 * barrier-aligned, so results are checked only after all workers join.
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

    private static <X extends Throwable> X thrown(Class<X> type, Body body) {
        try {
            body.run();
        } catch (Throwable t) {
            if (type.isInstance(t)) return type.cast(t);
            throw new AssertionError("expected " + type.getSimpleName() + " but got " + t, t);
        }
        throw new AssertionError("expected " + type.getSimpleName() + " but nothing was thrown");
    }

    /** Independent check-side formatter (never the codec under test). */
    private static final DateTimeFormatter CHECK_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss", Locale.ROOT).withZone(ZoneOffset.UTC);

    private static long epochOf(String stamp) {
        return LocalDateTime.parse(stamp.replace(' ', 'T')).toInstant(ZoneOffset.UTC).toEpochMilli();
    }

    private static final int CONTROLLERS = 8;
    private static final int SWIPES = 6000;

    /** Controller c stamps its swipes on its own day of March 2026. */
    private static String stampFor(int controller, int i) {
        int sec = 6 * 3600 + i;   // overnight batch runs from 06:00:00 UTC
        return String.format(Locale.ROOT, "2026-03-%02d %02d:%02d:%02d",
                2 + controller, sec / 3600, (sec / 60) % 60, sec % 60);
    }

    public static void main(String[] args) {
        // Ingest hosts are headless UTC/ROOT images; pin the same here so the
        // expected values are identical wherever the suite runs.
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));

        test("single_controller_replay_is_byte_perfect", () -> {
            List<String> stamps = List.of(
                    "2026-03-02 06:00:00", "2026-03-02 06:59:59", "2026-03-02 23:59:59",
                    "2026-12-31 23:59:59", "2026-01-01 00:00:00");
            long[] got = SwipeLog.normalizeBatch(stamps);
            eq("batch size", stamps.size(), got.length);
            for (int i = 0; i < stamps.size(); i++) {
                eq("epoch of " + stamps.get(i), epochOf(stamps.get(i)), got[i]);
            }
        });

        test("audit_lines_round_trip_for_one_upload", () -> {
            List<String> stamps = List.of(
                    "2026-03-02 06:15:09", "2026-03-02 07:03:44", "2026-03-02 07:59:59");
            long[] epochs = SwipeLog.normalizeBatch(stamps);
            List<String> expected = new ArrayList<>();
            for (String s : stamps) {
                expected.add(epochOf(s) + "  " + s);
            }
            eq("audit lines", expected, SwipeLog.auditLines(epochs));
        });

        test("malformed_stamps_are_rejected", () -> {
            thrown(IllegalArgumentException.class, () -> StampCodec.parseEpochMillis("2026-02-30 08:00:00"));
            thrown(IllegalArgumentException.class, () -> StampCodec.parseEpochMillis("2026-03-02 25:10:00"));
            thrown(IllegalArgumentException.class, () -> StampCodec.parseEpochMillis("badge swipe"));
        });

        test("simultaneous_uploads_normalize_exactly", () -> {
            List<List<String>> uploads = new ArrayList<>();
            long[][] expected = new long[CONTROLLERS][SWIPES];
            for (int c = 0; c < CONTROLLERS; c++) {
                List<String> batch = new ArrayList<>(SWIPES);
                for (int i = 0; i < SWIPES; i++) {
                    String stamp = stampFor(c, i);
                    batch.add(stamp);
                    expected[c][i] = epochOf(stamp);
                }
                uploads.add(batch);
            }
            long anomalies = 0;
            for (int round = 0; round < 3; round++) {
                long[] bad = new long[CONTROLLERS];
                CyclicBarrier uploadWindow = new CyclicBarrier(CONTROLLERS);
                Thread[] workers = new Thread[CONTROLLERS];
                for (int c = 0; c < CONTROLLERS; c++) {
                    final int cc = c;
                    workers[cc] = new Thread(() -> {
                        try {
                            uploadWindow.await();
                            long[] got = SwipeLog.normalizeBatch(uploads.get(cc));
                            for (int i = 0; i < SWIPES; i++) {
                                if (got[i] != expected[cc][i]) bad[cc]++;
                            }
                        } catch (Throwable t) {
                            bad[cc] += SWIPES;   // upload aborted mid-batch
                        }
                    }, "controller-" + cc);
                    workers[cc].start();
                }
                for (Thread w : workers) {
                    w.join();
                }
                for (long b : bad) {
                    anomalies += b;
                }
            }
            eq("anomalous swipes across three upload windows", 0L, anomalies);
        });

        test("simultaneous_audit_reports_round_trip", () -> {
            final int reports = 4000;
            long[][] epochs = new long[CONTROLLERS][reports];
            String[][] expectedLines = new String[CONTROLLERS][reports];
            for (int c = 0; c < CONTROLLERS; c++) {
                long base = LocalDateTime.of(2026, 4, 3 + c, 22, 0, 0)
                        .toInstant(ZoneOffset.UTC).toEpochMilli();
                for (int i = 0; i < reports; i++) {
                    long e = base + i * 1000L;
                    epochs[c][i] = e;
                    expectedLines[c][i] = e + "  " + CHECK_FORMAT.format(Instant.ofEpochMilli(e));
                }
            }
            long anomalies = 0;
            for (int round = 0; round < 3; round++) {
                long[] bad = new long[CONTROLLERS];
                CyclicBarrier reportWindow = new CyclicBarrier(CONTROLLERS);
                Thread[] workers = new Thread[CONTROLLERS];
                for (int c = 0; c < CONTROLLERS; c++) {
                    final int cc = c;
                    workers[cc] = new Thread(() -> {
                        try {
                            reportWindow.await();
                            List<String> lines = SwipeLog.auditLines(epochs[cc]);
                            for (int i = 0; i < reports; i++) {
                                if (!expectedLines[cc][i].equals(lines.get(i))) bad[cc]++;
                            }
                        } catch (Throwable t) {
                            bad[cc] += reports;   // report aborted mid-batch
                        }
                    }, "report-" + cc);
                    workers[cc].start();
                }
                for (Thread w : workers) {
                    w.join();
                }
                for (long b : bad) {
                    anomalies += b;
                }
            }
            eq("anomalous report lines across three windows", 0L, anomalies);
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
