import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AccessDeniedException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/** Protected acceptance tests. Run with: java TestMain.java */
public final class TestMain {
    private static int passed;
    private static int failed;

    @FunctionalInterface
    interface Body {
        void run() throws Exception;
    }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable failure) {
            failed++;
            System.out.println("FAIL " + name + ": " + failure);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(
                    what + ": expected <" + expected + "> but got <" + actual + ">");
        }
    }

    private static void same(String what, Object expected, Object actual) {
        if (expected != actual) {
            throw new AssertionError(what + ": expected the original object");
        }
    }

    private static void yes(String what, boolean condition) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    private static <T extends Throwable> T thrown(Class<T> type, Body body) {
        try {
            body.run();
        } catch (Throwable failure) {
            if (type.isInstance(failure)) {
                return type.cast(failure);
            }
            throw new AssertionError(
                    "expected " + type.getSimpleName() + " but got " + failure, failure);
        }
        throw new AssertionError(
                "expected " + type.getSimpleName() + " but nothing was thrown");
    }

    private static void suppressed(Throwable failure, Throwable... expected) {
        Throwable[] actual = failure.getSuppressed();
        eq("suppressed failure count", expected.length, actual.length);
        for (int i = 0; i < expected.length; i++) {
            same("suppressed failure " + i, expected[i], actual[i]);
        }
    }

    public static void main(String[] args) {
        test("entry_is_streamed_lazily", () -> {
            Rig rig = new Rig("abcdefgh");
            InputStream stream = rig.streamer.open(rig.path, "report.csv");

            eq("opening does not read entry bytes", 0, rig.readCalls);
            eq("only owner and entry opened", List.of("owner-open", "entry-open"), rig.events);
            eq("first chunk", "abc",
                    new String(stream.readNBytes(3), StandardCharsets.UTF_8));
            yes("source was read incrementally", rig.offset == 3 && rig.readCalls >= 2);
            eq("remainder", "defgh",
                    new String(stream.readAllBytes(), StandardCharsets.UTF_8));

            stream.close();
            eq("ordered cleanup",
                    List.of("owner-open", "entry-open", "entry-close", "owner-close", "delete"),
                    rig.events);
        });

        test("windows_like_lock_is_released_before_delete", () -> {
            Rig rig = new Rig("payload");
            InputStream stream = rig.streamer.open(rig.path, "report.csv");
            stream.close();

            yes("archive owner is closed", !rig.locked);
            yes("stage was deleted", rig.deleted);
            eq("owner closed exactly once", 1, rig.ownerCloseCalls);
        });

        test("close_is_idempotent", () -> {
            Rig rig = new Rig("payload");
            InputStream stream = rig.streamer.open(rig.path, "report.csv");
            stream.close();
            stream.close();

            eq("entry closed once", 1, rig.entryCloseCalls);
            eq("owner closed once", 1, rig.ownerCloseCalls);
            eq("delete attempted once", 1, rig.deleteCalls);
        });

        test("read_failure_is_not_rewritten_or_eagerly_closed", () -> {
            Rig rig = new Rig("abcdef");
            IOException readFailure = new IOException("decompress failed");
            rig.readFailure = readFailure;
            rig.readFailureAt = 2;
            InputStream stream = rig.streamer.open(rig.path, "report.csv");

            eq("open remains lazy", 0, rig.readCalls);
            same("read failure", readFailure,
                    thrown(IOException.class, stream::readAllBytes));
            yes("a read error does not silently close the caller's stream", rig.locked);

            stream.close();
            yes("explicit close still cleans the stage", rig.deleted && !rig.locked);
        });

        test("entry_open_failure_cleans_owner_and_preserves_error", () -> {
            Rig rig = new Rig("unused");
            IOException openFailure = new IOException("bad central directory");
            rig.entryOpenFailure = openFailure;

            same("open failure", openFailure,
                    thrown(IOException.class,
                            () -> rig.streamer.open(rig.path, "report.csv")));
            eq("setup cleanup order",
                    List.of("owner-open", "entry-open", "owner-close", "delete"), rig.events);
            yes("failed setup deletes stage", rig.deleted && !rig.locked);
        });

        test("entry_close_failure_stays_primary_but_cleanup_continues", () -> {
            Rig rig = new Rig("payload");
            IOException entryCloseFailure = new IOException("entry checksum close failed");
            rig.entryCloseFailure = entryCloseFailure;
            InputStream stream = rig.streamer.open(rig.path, "report.csv");

            IOException actual = thrown(IOException.class, stream::close);
            same("entry close remains primary", entryCloseFailure, actual);
            suppressed(actual);
            yes("later cleanup still happened", rig.deleted && !rig.locked);
            eq("ordered cleanup",
                    List.of("owner-open", "entry-open", "entry-close", "owner-close", "delete"),
                    rig.events);
        });

        test("owner_close_failure_is_reported_after_delete_attempt", () -> {
            Rig rig = new Rig("payload");
            IOException ownerCloseFailure = new IOException("zip close failed");
            rig.ownerCloseFailure = ownerCloseFailure;
            InputStream stream = rig.streamer.open(rig.path, "report.csv");

            same("owner close failure", ownerCloseFailure,
                    thrown(IOException.class, stream::close));
            yes("owner released before reporting close failure", !rig.locked);
            yes("delete was still attempted", rig.deleted);
        });

        test("all_close_failures_keep_stable_primary_and_suppressed_order", () -> {
            Rig rig = new Rig("payload");
            IOException entryFailure = new IOException("entry close failed");
            IOException ownerFailure = new IOException("owner close failed");
            IOException deleteFailure = new IOException("delete failed");
            rig.entryCloseFailure = entryFailure;
            rig.ownerCloseFailure = ownerFailure;
            rig.deleteFailure = deleteFailure;
            InputStream stream = rig.streamer.open(rig.path, "report.csv");

            IOException actual = thrown(IOException.class, stream::close);
            same("first close failure remains primary", entryFailure, actual);
            suppressed(actual, ownerFailure, deleteFailure);
            eq("all cleanup steps attempted",
                    List.of("owner-open", "entry-open", "entry-close", "owner-close", "delete"),
                    rig.events);
        });

        test("setup_cleanup_failures_are_suppressed_on_open_error", () -> {
            Rig rig = new Rig("unused");
            IOException openFailure = new IOException("entry missing");
            IOException ownerFailure = new IOException("owner close failed");
            IOException deleteFailure = new IOException("delete failed");
            rig.entryOpenFailure = openFailure;
            rig.ownerCloseFailure = ownerFailure;
            rig.deleteFailure = deleteFailure;

            IOException actual = thrown(IOException.class,
                    () -> rig.streamer.open(rig.path, "missing.csv"));
            same("open failure remains primary", openFailure, actual);
            suppressed(actual, ownerFailure, deleteFailure);
            eq("all setup cleanup steps attempted",
                    List.of("owner-open", "entry-open", "owner-close", "delete"), rig.events);
        });

        System.out.println("RESULT " + passed + " passed, " + failed + " failed");
        if (failed != 0) {
            System.exit(1);
        }
    }

    private static final class Rig implements FileLockTracker {
        private final Path path = Path.of("staging", "report-bundle.zip");
        private final BundleStreamer streamer = new BundleStreamer(this);
        private final byte[] bytes;
        private final List<String> events = new ArrayList<>();

        private boolean locked;
        private boolean deleted;
        private int offset;
        private int readCalls;
        private int entryCloseCalls;
        private int ownerCloseCalls;
        private int deleteCalls;
        private int readFailureAt = Integer.MAX_VALUE;
        private IOException readFailure;
        private IOException entryOpenFailure;
        private IOException entryCloseFailure;
        private IOException ownerCloseFailure;
        private IOException deleteFailure;

        private Rig(String content) {
            bytes = content.getBytes(StandardCharsets.UTF_8);
        }

        @Override
        public ArchiveOwner open(Path archive) {
            eq("opened path", path, archive);
            if (locked) {
                throw new IllegalStateException("test owner already open");
            }
            events.add("owner-open");
            locked = true;
            return new FakeOwner();
        }

        @Override
        public void delete(Path archive) throws IOException {
            eq("deleted path", path, archive);
            events.add("delete");
            deleteCalls++;
            if (locked) {
                throw new AccessDeniedException(
                        archive.toString(), null, "simulated Windows sharing violation");
            }
            if (deleteFailure != null) {
                throw deleteFailure;
            }
            deleted = true;
        }

        private final class FakeOwner implements ArchiveOwner {
            private boolean closed;

            @Override
            public InputStream openEntry(String entryName) throws IOException {
                yes("entry name",
                        "report.csv".equals(entryName) || "missing.csv".equals(entryName));
                events.add("entry-open");
                if (entryOpenFailure != null) {
                    throw entryOpenFailure;
                }
                return new FakeEntryStream();
            }

            @Override
            public void close() throws IOException {
                if (closed) {
                    return;
                }
                closed = true;
                events.add("owner-close");
                ownerCloseCalls++;
                locked = false;
                if (ownerCloseFailure != null) {
                    throw ownerCloseFailure;
                }
            }
        }

        private final class FakeEntryStream extends InputStream {
            private boolean closed;

            @Override
            public int read() throws IOException {
                byte[] one = new byte[1];
                int count = read(one, 0, 1);
                return count < 0 ? -1 : one[0] & 0xff;
            }

            @Override
            public int read(byte[] target, int start, int length) throws IOException {
                Objects.checkFromIndexSize(start, length, target.length);
                if (closed) {
                    throw new IOException("entry stream closed");
                }
                readCalls++;
                if (readFailure != null && offset >= readFailureAt) {
                    throw readFailure;
                }
                if (offset == bytes.length) {
                    return -1;
                }
                int beforeFailure = readFailureAt - offset;
                int count = Math.min(length, Math.min(2, bytes.length - offset));
                count = Math.min(count, beforeFailure);
                if (count == 0 && readFailure != null) {
                    throw readFailure;
                }
                System.arraycopy(bytes, offset, target, start, count);
                offset += count;
                return count;
            }

            @Override
            public void close() throws IOException {
                if (closed) {
                    return;
                }
                closed = true;
                events.add("entry-close");
                entryCloseCalls++;
                if (entryCloseFailure != null) {
                    throw entryCloseFailure;
                }
            }
        }
    }
}
