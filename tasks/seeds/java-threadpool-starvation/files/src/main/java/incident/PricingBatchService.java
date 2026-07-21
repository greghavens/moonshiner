package incident;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/** Coordinates bounded pricing work for an incoming batch. */
public final class PricingBatchService implements AutoCloseable {
    @FunctionalInterface
    public interface PriceClient {
        int loadPriceCents(String sku) throws Exception;
    }

    public record ExecutorSnapshot(
            String domain,
            int maxWorkers,
            int activeWorkers,
            int queuedTasks,
            int queueCapacity,
            String queueType,
            String rejectionPolicy) {
    }

    private final PriceClient priceClient;
    private final ThreadPoolExecutor requestExecutor;

    public PricingBatchService(int workers, int queueCapacity, PriceClient priceClient) {
        if (workers < 1) {
            throw new IllegalArgumentException("workers must be positive");
        }
        if (queueCapacity < 1) {
            throw new IllegalArgumentException("queueCapacity must be positive");
        }

        this.priceClient = Objects.requireNonNull(priceClient, "priceClient");
        this.requestExecutor = newBoundedExecutor(
                workers, queueCapacity, "pricing-request-");
    }

    private static ThreadPoolExecutor newBoundedExecutor(
            int workers, int queueCapacity, String threadPrefix) {
        AtomicInteger sequence = new AtomicInteger();
        ThreadFactory threadFactory = task -> {
            Thread thread = new Thread(task, threadPrefix + sequence.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        };
        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                workers,
                workers,
                0L,
                TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(queueCapacity),
                threadFactory,
                new ThreadPoolExecutor.CallerRunsPolicy());
        // Stable worker occupancy also makes production diagnostics easier to read.
        executor.prestartAllCoreThreads();
        return executor;
    }

    public List<Integer> priceBatch(List<String> skus)
            throws InterruptedException, ExecutionException {
        Objects.requireNonNull(skus, "skus");
        List<Future<Integer>> pending = new ArrayList<>(skus.size());

        try {
            for (String sku : skus) {
                Objects.requireNonNull(sku, "sku");
                pending.add(requestExecutor.submit(() -> priceOne(sku)));
            }

            List<Integer> prices = new ArrayList<>(pending.size());
            for (Future<Integer> price : pending) {
                prices.add(price.get());
            }
            return List.copyOf(prices);
        } catch (InterruptedException | ExecutionException | RuntimeException failure) {
            for (Future<Integer> price : pending) {
                price.cancel(true);
            }
            throw failure;
        }
    }

    private int priceOne(String sku) throws Exception {
        // The client is blocking, so dispatch it instead of tying up the caller.
        return requestExecutor.submit(() -> priceClient.loadPriceCents(sku)).get();
    }

    public List<ExecutorSnapshot> executorSnapshots() {
        return List.of(snapshot("requests", requestExecutor));
    }

    private static ExecutorSnapshot snapshot(String domain, ThreadPoolExecutor executor) {
        int queueCapacity = executor.getQueue().size()
                + executor.getQueue().remainingCapacity();
        return new ExecutorSnapshot(
                domain,
                executor.getMaximumPoolSize(),
                executor.getActiveCount(),
                executor.getQueue().size(),
                queueCapacity,
                executor.getQueue().getClass().getSimpleName(),
                executor.getRejectedExecutionHandler().getClass().getSimpleName());
    }

    @Override
    public void close() {
        stop(requestExecutor);
    }

    private static void stop(ThreadPoolExecutor executor) {
        executor.shutdownNow();
        try {
            executor.awaitTermination(2, TimeUnit.SECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }
}
