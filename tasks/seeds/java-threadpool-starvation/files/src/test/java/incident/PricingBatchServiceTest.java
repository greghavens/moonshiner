package incident;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

public final class PricingBatchServiceTest {
    private static final long COMPLETION_MILLIS = 3_000L;

    public static void main(String[] args) throws Exception {
        nestedWorkCompletesWhenEveryRequestWorkerIsOccupied();
        multiItemBatchHasBoundedCompletion();
        dependencyFailureRetainsExceptionBehavior();
        bothExecutionDomainsPreserveBoundedBackpressure();
        closeTerminatesEveryOwnedExecutor();
        System.out.println("PricingBatchServiceTest: PASS");
    }

    private static void nestedWorkCompletesWhenEveryRequestWorkerIsOccupied()
            throws Exception {
        try (PricingBatchService service = new PricingBatchService(
                1, 1, sku -> {
                    String worker = Thread.currentThread().getName();
                    if (!worker.startsWith("pricing-dependency-")) {
                        throw new AssertionError(
                                "price client ran in the wrong domain: " + worker);
                    }
                    return 1_099;
                })) {
            List<Integer> actual = callWithinBound(service, List.of("coffee"));
            assertEquals(List.of(1_099), actual, "single-worker price result");
        }
    }

    private static void multiItemBatchHasBoundedCompletion() throws Exception {
        Map<String, Integer> prices = Map.of(
                "a", 101,
                "b", 202,
                "c", 303,
                "d", 404,
                "e", 505,
                "f", 606);
        try (PricingBatchService service = new PricingBatchService(
                2, 2, sku -> prices.get(sku))) {
            List<Integer> actual = callWithinBound(
                    service, List.of("a", "b", "c", "d", "e", "f"));
            assertEquals(
                    List.of(101, 202, 303, 404, 505, 606),
                    actual,
                    "batch must retain input order");
        }
    }

    private static void dependencyFailureRetainsExceptionBehavior()
            throws Exception {
        Exception dependencyFailure = new Exception("price backend unavailable");
        try (PricingBatchService service = new PricingBatchService(
                1, 1, sku -> {
                    throw dependencyFailure;
                })) {
            try {
                service.priceBatch(List.of("coffee"));
                throw new AssertionError("dependency failure was not propagated");
            } catch (ExecutionException requestFailure) {
                Object nested = requestFailure.getCause();
                if (!(nested instanceof ExecutionException dependencyWrapper)) {
                    throw new AssertionError(
                            "request future must retain the dependency wrapper",
                            requestFailure);
                }
                if (dependencyWrapper.getCause() != dependencyFailure) {
                    throw new AssertionError(
                            "dependency cause identity was not retained",
                            requestFailure);
                }
            }
        }
    }

    private static void bothExecutionDomainsPreserveBoundedBackpressure() {
        try (PricingBatchService service = new PricingBatchService(
                2, 3, sku -> 42)) {
            Map<String, PricingBatchService.ExecutorSnapshot> byDomain =
                    new LinkedHashMap<>();
            for (PricingBatchService.ExecutorSnapshot snapshot
                    : service.executorSnapshots()) {
                byDomain.put(snapshot.domain(), snapshot);
            }

            assertEquals(
                    List.of("requests", "dependencies"),
                    new ArrayList<>(byDomain.keySet()),
                    "separate execution domains");
            for (PricingBatchService.ExecutorSnapshot snapshot : byDomain.values()) {
                assertEquals(2, snapshot.maxWorkers(),
                        snapshot.domain() + " fixed worker bound");
                assertEquals(3, snapshot.queueCapacity(),
                        snapshot.domain() + " queue bound");
                assertEquals("ArrayBlockingQueue", snapshot.queueType(),
                        snapshot.domain() + " queue type");
                assertEquals("CallerRunsPolicy", snapshot.rejectionPolicy(),
                        snapshot.domain() + " caller backpressure");
            }
        }
    }

    private static void closeTerminatesEveryOwnedExecutor() {
        PricingBatchService service = new PricingBatchService(2, 2, sku -> 42);
        service.close();

        List<String> liveWorkers = new ArrayList<>();
        for (Thread thread : Thread.getAllStackTraces().keySet()) {
            if (thread.isAlive()
                    && (thread.getName().startsWith("pricing-request-")
                    || thread.getName().startsWith("pricing-dependency-"))) {
                liveWorkers.add(thread.getName());
            }
        }
        liveWorkers.sort(String::compareTo);
        assertEquals(List.of(), liveWorkers, "close must terminate owned workers");
    }

    private static List<Integer> callWithinBound(
            PricingBatchService service, List<String> skus) throws Exception {
        ExecutorService caller = Executors.newSingleThreadExecutor();
        Future<List<Integer>> call = caller.submit(() -> service.priceBatch(skus));
        try {
            return call.get(COMPLETION_MILLIS, TimeUnit.MILLISECONDS);
        } catch (TimeoutException timeout) {
            PricingBatchService.ExecutorSnapshot request =
                    service.executorSnapshots().get(0);
            String dump = pricingThreadDump();
            if (request.activeWorkers() < 1 || request.queuedTasks() < 1) {
                throw new AssertionError(
                        "batch timed out without the expected saturated queue: "
                                + request + System.lineSeparator() + dump,
                        timeout);
            }
            if (!dump.contains("WAITING")
                    || (!dump.contains("FutureTask.get")
                    && !dump.contains("FutureTask.awaitDone"))) {
                throw new AssertionError(
                        "batch timed out without workers waiting on nested futures: "
                                + request + System.lineSeparator() + dump,
                        timeout);
            }
            throw new AssertionError(
                    "bounded completion violated; nested work is queued behind "
                            + "its blocked request worker: " + request
                            + System.lineSeparator() + dump,
                    timeout);
        } finally {
            call.cancel(true);
            caller.shutdownNow();
            caller.awaitTermination(2, TimeUnit.SECONDS);
        }
    }

    private static String pricingThreadDump() {
        List<Map.Entry<Thread, StackTraceElement[]>> threads =
                new ArrayList<>(Thread.getAllStackTraces().entrySet());
        threads.sort(Comparator.comparing(entry -> entry.getKey().getName()));

        StringBuilder dump = new StringBuilder();
        for (Map.Entry<Thread, StackTraceElement[]> entry : threads) {
            Thread thread = entry.getKey();
            if (!thread.getName().startsWith("pricing-request-")) {
                continue;
            }
            dump.append('"').append(thread.getName()).append("\" ")
                    .append(thread.getState()).append(System.lineSeparator());
            for (StackTraceElement frame : entry.getValue()) {
                dump.append("  at ").append(frame).append(System.lineSeparator());
            }
        }
        return dump.toString();
    }

    private static void assertEquals(Object expected, Object actual, String label) {
        if (!expected.equals(actual)) {
            throw new AssertionError(
                    label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }
}
