import java.util.Objects;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Protected acceptance suite. Run with: java TestMain.java */
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

    private static void eq(String label, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected <" + expected + "> but got <"
                    + actual + ">");
        }
    }

    private static void yes(String label, boolean condition) {
        if (!condition) {
            throw new AssertionError(label);
        }
    }

    private static void throwsMessage(String label, String expected, Body body) {
        try {
            body.run();
        } catch (IllegalArgumentException failure) {
            eq(label + " message", expected, failure.getMessage());
            return;
        } catch (Exception failure) {
            throw new AssertionError(label + ": wrong exception " + failure, failure);
        }
        throw new AssertionError(label + ": no exception");
    }

    public static void main(String[] args) {
        test("same_partition_duplicates_are_suppressed", () -> {
            PartitionedDeduplicator dedup = new PartitionedDeduplicator(4);
            eq("first event", DedupDecision.ACCEPTED, dedup.observe("orders-0", 100));
            eq("repeat", DedupDecision.DUPLICATE, dedup.observe("orders-0", 100));
            eq("high watermark", 100L, dedup.highWatermark("orders-0").orElseThrow());
            eq("tracked", 1, dedup.trackedEventCount());
            eq("partitions", 1, dedup.partitionCount());
        });

        test("unseen_late_arrivals_inside_the_window_are_accepted", () -> {
            PartitionedDeduplicator dedup = new PartitionedDeduplicator(4);
            eq("100", DedupDecision.ACCEPTED, dedup.observe("orders-0", 100));
            eq("103", DedupDecision.ACCEPTED, dedup.observe("orders-0", 103));
            eq("101 late", DedupDecision.ACCEPTED, dedup.observe("orders-0", 101));
            eq("102 late", DedupDecision.ACCEPTED, dedup.observe("orders-0", 102));
            eq("101 repeat", DedupDecision.DUPLICATE, dedup.observe("orders-0", 101));
            eq("outside retained range", DedupDecision.TOO_OLD,
                    dedup.observe("orders-0", 99));
            eq("bounded retained count", 4, dedup.trackedEventCount("orders-0"));
        });

        test("advancing_a_partition_evicts_only_its_old_range", () -> {
            PartitionedDeduplicator dedup = new PartitionedDeduplicator(3);
            dedup.observe("busy", 10);
            dedup.observe("busy", 11);
            dedup.observe("busy", 12);
            eq("advance", DedupDecision.ACCEPTED, dedup.observe("busy", 13));
            eq("evicted offset", DedupDecision.TOO_OLD, dedup.observe("busy", 10));
            eq("retained boundary", DedupDecision.DUPLICATE, dedup.observe("busy", 11));
            eq("bounded after advance", 3, dedup.trackedEventCount("busy"));

            eq("leap", DedupDecision.ACCEPTED, dedup.observe("busy", 16));
            eq("new late 15", DedupDecision.ACCEPTED, dedup.observe("busy", 15));
            eq("new late 14", DedupDecision.ACCEPTED, dedup.observe("busy", 14));
            eq("old high was evicted", DedupDecision.TOO_OLD, dedup.observe("busy", 13));
            eq("bounded after leap", 3, dedup.trackedEventCount("busy"));
        });

        test("partition_windows_advance_independently", () -> {
            PartitionedDeduplicator dedup = new PartitionedDeduplicator(3);
            eq("east 7", DedupDecision.ACCEPTED, dedup.observe("east", 7));
            eq("west 7", DedupDecision.ACCEPTED, dedup.observe("west", 7));
            eq("east advances", DedupDecision.ACCEPTED, dedup.observe("east", 20));
            eq("west 7 remains", DedupDecision.DUPLICATE, dedup.observe("west", 7));
            eq("west unseen late", DedupDecision.ACCEPTED, dedup.observe("west", 6));
            eq("east 7 aged out", DedupDecision.TOO_OLD, dedup.observe("east", 7));
            eq("east high", 20L, dedup.highWatermark("east").orElseThrow());
            eq("west high", 7L, dedup.highWatermark("west").orElseThrow());
        });

        test("checkpoint_restores_gaps_high_watermarks_and_eviction", () -> {
            PartitionedDeduplicator before = new PartitionedDeduplicator(4);
            before.observe("alpha", 100);
            before.observe("alpha", 103);
            before.observe("beta", 500);
            before.observe("beta", 502);

            PartitionedDeduplicator restored =
                    PartitionedDeduplicator.restore(before.checkpoint());
            eq("window size", 4, restored.windowSize());
            eq("alpha high", 103L, restored.highWatermark("alpha").orElseThrow());
            eq("beta high", 502L, restored.highWatermark("beta").orElseThrow());
            eq("known alpha", DedupDecision.DUPLICATE, restored.observe("alpha", 100));
            eq("alpha gap", DedupDecision.ACCEPTED, restored.observe("alpha", 101));
            eq("alpha old", DedupDecision.TOO_OLD, restored.observe("alpha", 99));
            eq("known beta", DedupDecision.DUPLICATE, restored.observe("beta", 502));
            eq("beta gap", DedupDecision.ACCEPTED, restored.observe("beta", 501));
            eq("beta old", DedupDecision.TOO_OLD, restored.observe("beta", 498));
        });

        test("restored_late_arrivals_remain_partition_local", () -> {
            PartitionedDeduplicator before = new PartitionedDeduplicator(4);
            before.observe("alpha", 41);
            before.observe("beta", 42);

            PartitionedDeduplicator restored =
                    PartitionedDeduplicator.restore(before.checkpoint());
            eq("beta late 41", DedupDecision.ACCEPTED, restored.observe("beta", 41));
            eq("alpha advance 42", DedupDecision.ACCEPTED, restored.observe("alpha", 42));
            eq("alpha 41 retained", DedupDecision.DUPLICATE,
                    restored.observe("alpha", 41));
            eq("beta 42 retained", DedupDecision.DUPLICATE,
                    restored.observe("beta", 42));
        });

        test("checkpoint_text_is_stable_and_utf8_safe", () -> {
            PartitionedDeduplicator first = new PartitionedDeduplicator(5);
            first.observe("rack/β west", 12);
            first.observe("rack/β west", 10);
            first.observe("plain", 3);

            PartitionedDeduplicator second = new PartitionedDeduplicator(5);
            second.observe("plain", 3);
            second.observe("rack/β west", 10);
            second.observe("rack/β west", 12);
            eq("deterministic checkpoint", first.checkpoint(), second.checkpoint());

            PartitionedDeduplicator restored =
                    PartitionedDeduplicator.restore(first.checkpoint());
            eq("unicode known", DedupDecision.DUPLICATE,
                    restored.observe("rack/β west", 10));
            eq("unicode gap", DedupDecision.ACCEPTED,
                    restored.observe("rack/β west", 11));
        });

        test("concurrent_observation_is_linearizable_and_partitioned", () -> {
            PartitionedDeduplicator samePartition = new PartitionedDeduplicator(8);
            AtomicInteger accepted = new AtomicInteger();
            AtomicReference<Throwable> workerFailure = new AtomicReference<>();
            runTogether(12, index -> {
                if (samePartition.observe("shared", 900) == DedupDecision.ACCEPTED) {
                    accepted.incrementAndGet();
                }
            }, workerFailure);
            rethrowWorkerFailure(workerFailure);
            eq("one winner for duplicate storm", 1, accepted.get());
            eq("one retained shared event", 1, samePartition.trackedEventCount());

            PartitionedDeduplicator manyPartitions = new PartitionedDeduplicator(2);
            AtomicInteger partitionAccepted = new AtomicInteger();
            workerFailure.set(null);
            runTogether(12, index -> {
                if (manyPartitions.observe("partition-" + index, 900)
                        == DedupDecision.ACCEPTED) {
                    partitionAccepted.incrementAndGet();
                }
            }, workerFailure);
            rethrowWorkerFailure(workerFailure);
            eq("one first delivery per partition", 12, partitionAccepted.get());
            eq("all partitions retained", 12, manyPartitions.trackedEventCount());
        });

        test("invalid_inputs_do_not_create_state", () -> {
            throwsMessage("window", "windowSize must be >= 1",
                    () -> new PartitionedDeduplicator(0));
            PartitionedDeduplicator dedup = new PartitionedDeduplicator(3);
            throwsMessage("null partition", "partition must not be empty",
                    () -> dedup.observe(null, 1));
            throwsMessage("empty partition", "partition must not be empty",
                    () -> dedup.observe("", 1));
            throwsMessage("negative sequence", "sequence must be >= 0",
                    () -> dedup.observe("valid", -1));
            throwsMessage("null checkpoint", "checkpoint must not be null",
                    () -> PartitionedDeduplicator.restore(null));
            throwsMessage("bad checkpoint", "invalid checkpoint",
                    () -> PartitionedDeduplicator.restore("not-a-checkpoint"));
            eq("no invalid state", 0, dedup.trackedEventCount());
            eq("no invalid partition", 0, dedup.partitionCount());
            yes("unknown high watermark", dedup.highWatermark("valid").isEmpty());
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed != 0) {
            System.exit(1);
        }
    }

    @FunctionalInterface
    interface IndexedBody {
        void run(int index) throws Exception;
    }

    private static void runTogether(int count, IndexedBody body,
                                    AtomicReference<Throwable> workerFailure)
            throws InterruptedException {
        CountDownLatch ready = new CountDownLatch(count);
        CountDownLatch start = new CountDownLatch(1);
        Thread[] threads = new Thread[count];
        for (int index = 0; index < count; index++) {
            int workerIndex = index;
            threads[index] = new Thread(() -> {
                ready.countDown();
                try {
                    start.await();
                    body.run(workerIndex);
                } catch (Throwable failure) {
                    workerFailure.compareAndSet(null, failure);
                }
            }, "dedup-test-" + index);
            threads[index].start();
        }
        ready.await();
        start.countDown();
        for (Thread thread : threads) {
            thread.join();
        }
    }

    private static void rethrowWorkerFailure(AtomicReference<Throwable> failure) {
        if (failure.get() != null) {
            throw new AssertionError("worker failed", failure.get());
        }
    }
}
