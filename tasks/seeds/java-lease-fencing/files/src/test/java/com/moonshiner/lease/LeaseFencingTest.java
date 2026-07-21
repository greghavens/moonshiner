package com.moonshiner.lease;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public final class LeaseFencingTest {
    private static final Duration TEN_SECONDS = Duration.ofSeconds(10L);

    private LeaseFencingTest() {
    }

    public static void main(String[] args) {
        List<String> failures = new ArrayList<String>();

        run(failures, "renewal keeps its token and extends from renewal time", new CheckedTest() {
            @Override
            public void run() {
                MutableClock clock = new MutableClock(Instant.parse("2024-01-01T00:00:00Z"));
                LeaseCoordinator coordinator = new LeaseCoordinator(clock);
                FencedResultStore store = new FencedResultStore();
                LeaseWorker first = worker("worker-a", coordinator, store);
                LeaseWorker contender = worker("worker-b", coordinator, store);

                assertTrue(first.tryAcquire(), "first worker should acquire");
                Lease original = requiredLease(first);
                clock.advance(Duration.ofSeconds(6L));
                first.renew();
                Lease renewed = requiredLease(first);

                assertEquals(original.fencingToken(), renewed.fencingToken());
                assertEquals(Instant.parse("2024-01-01T00:00:16Z"), renewed.expiresAt());
                clock.advance(Duration.ofSeconds(9L));
                assertFalse(contender.tryAcquire(), "renewed lease must remain exclusive");
                clock.advance(Duration.ofSeconds(1L));
                assertTrue(contender.tryAcquire(), "lease expires at its exact deadline");
            }
        });

        run(failures, "expired and superseded epochs cannot renew", new CheckedTest() {
            @Override
            public void run() {
                MutableClock clock = new MutableClock(Instant.parse("2024-02-01T12:00:00Z"));
                LeaseCoordinator coordinator = new LeaseCoordinator(clock);
                FencedResultStore store = new FencedResultStore();
                LeaseWorker oldOwner = worker("worker-a", coordinator, store);
                LeaseWorker newOwner = worker("worker-b", coordinator, store);

                assertTrue(oldOwner.tryAcquire(), "old owner should acquire");
                long oldToken = requiredLease(oldOwner).fencingToken();
                clock.advance(TEN_SECONDS);
                expectLeaseLost(new CheckedTest() {
                    @Override
                    public void run() {
                        oldOwner.renew();
                    }
                });
                assertTrue(newOwner.tryAcquire(), "new owner should take over at expiry");
                assertGreater(requiredLease(newOwner).fencingToken(), oldToken);
                expectLeaseLost(new CheckedTest() {
                    @Override
                    public void run() {
                        oldOwner.renew();
                    }
                });
            }
        });

        run(failures, "paused old owner cannot overwrite after takeover", new CheckedTest() {
            @Override
            public void run() {
                MutableClock clock = new MutableClock(Instant.parse("2024-03-01T00:00:00Z"));
                LeaseCoordinator coordinator = new LeaseCoordinator(clock);
                FencedResultStore store = new FencedResultStore();
                LeaseWorker pausedOwner = worker("worker-a", coordinator, store);
                LeaseWorker takeoverOwner = worker("worker-b", coordinator, store);

                assertTrue(pausedOwner.tryAcquire(), "old owner should acquire");
                pausedOwner.commit("old owner's initial result");

                clock.advance(TEN_SECONDS);
                assertTrue(takeoverOwner.tryAcquire(), "new owner should take over");
                takeoverOwner.commit("new owner's result");

                expectStaleToken(new CheckedTest() {
                    @Override
                    public void run() {
                        pausedOwner.commit("stale overwrite");
                    }
                });
                assertEquals("new owner's result", requiredResult(store));
            }
        });

        run(failures, "tokens are positive and monotonic per acquisition epoch", new CheckedTest() {
            @Override
            public void run() {
                MutableClock clock = new MutableClock(Instant.parse("2024-04-01T00:00:00Z"));
                LeaseCoordinator coordinator = new LeaseCoordinator(clock);
                FencedResultStore store = new FencedResultStore();
                LeaseWorker first = worker("worker-a", coordinator, store);
                LeaseWorker second = worker("worker-b", coordinator, store);
                LeaseWorker third = worker("worker-c", coordinator, store);

                assertTrue(first.tryAcquire(), "first worker should acquire");
                long firstToken = requiredLease(first).fencingToken();
                assertGreater(firstToken, 0L);

                clock.advance(Duration.ofSeconds(5L));
                assertFalse(second.tryAcquire(), "failed acquisition must not replace the owner");
                first.renew();
                assertEquals(firstToken, requiredLease(first).fencingToken());

                clock.advance(TEN_SECONDS);
                assertTrue(second.tryAcquire(), "second worker should acquire after renewal expiry");
                long secondToken = requiredLease(second).fencingToken();
                assertEquals(firstToken + 1L, secondToken);

                clock.advance(TEN_SECONDS);
                assertTrue(third.tryAcquire(), "third worker should acquire after expiry");
                long thirdToken = requiredLease(third).fencingToken();
                assertEquals(secondToken + 1L, thirdToken);
            }
        });

        run(failures, "current lease epoch may commit repeatedly", new CheckedTest() {
            @Override
            public void run() {
                MutableClock clock = new MutableClock(Instant.parse("2024-05-01T00:00:00Z"));
                LeaseCoordinator coordinator = new LeaseCoordinator(clock);
                FencedResultStore store = new FencedResultStore();
                LeaseWorker worker = worker("worker-a", coordinator, store);

                assertTrue(worker.tryAcquire(), "worker should acquire");
                worker.commit("first");
                worker.commit("second");

                assertEquals("second", requiredResult(store));
                assertEquals(requiredLease(worker).fencingToken(), store.highestAcceptedToken());
            }
        });

        if (!failures.isEmpty()) {
            for (String failure : failures) {
                System.err.println(failure);
            }
            throw new AssertionError(failures.size() + " test(s) failed");
        }
        System.out.println("All lease fencing tests passed.");
    }

    private static LeaseWorker worker(
            String ownerId, LeaseCoordinator coordinator, FencedResultStore store) {
        return new LeaseWorker(ownerId, TEN_SECONDS, coordinator, store);
    }

    private static Lease requiredLease(LeaseWorker worker) {
        Optional<Lease> lease = worker.lease();
        if (!lease.isPresent()) {
            throw new AssertionError("expected worker to hold a lease");
        }
        return lease.get();
    }

    private static String requiredResult(FencedResultStore store) {
        Optional<String> result = store.result();
        if (!result.isPresent()) {
            throw new AssertionError("expected a committed result");
        }
        return result.get();
    }

    private static void run(List<String> failures, String name, CheckedTest test) {
        try {
            test.run();
        } catch (Throwable failure) {
            failures.add(name + ": " + failure);
        }
    }

    private static void expectLeaseLost(CheckedTest test) {
        try {
            test.run();
            throw new AssertionError("expected LeaseLostException");
        } catch (LeaseLostException expected) {
            // Expected.
        }
    }

    private static void expectStaleToken(CheckedTest test) {
        try {
            test.run();
            throw new AssertionError("expected StaleFencingTokenException");
        } catch (StaleFencingTokenException expected) {
            // Expected.
        }
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void assertFalse(boolean condition, String message) {
        assertTrue(!condition, message);
    }

    private static void assertGreater(long actual, long lowerBound) {
        if (actual <= lowerBound) {
            throw new AssertionError("expected <" + actual + "> to be greater than <"
                    + lowerBound + ">");
        }
    }

    private static void assertEquals(long expected, long actual) {
        if (expected != actual) {
            throw new AssertionError("expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void assertEquals(Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError("expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private interface CheckedTest {
        void run();
    }

    private static final class MutableClock extends Clock {
        private Instant instant;

        private MutableClock(Instant instant) {
            this.instant = instant;
        }

        private void advance(Duration duration) {
            instant = instant.plus(duration);
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            if (ZoneOffset.UTC.equals(zone)) {
                return this;
            }
            return Clock.fixed(instant, zone);
        }

        @Override
        public Instant instant() {
            return instant;
        }
    }
}
