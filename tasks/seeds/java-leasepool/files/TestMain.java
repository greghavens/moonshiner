import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Acceptance tests for the CAD-kernel license-session pool.
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

    private static <X extends Throwable> X thrown(Class<X> type, Body body) {
        try {
            body.run();
        } catch (Throwable t) {
            if (type.isInstance(t)) return type.cast(t);
            throw new AssertionError("expected " + type.getSimpleName() + " but got " + t, t);
        }
        throw new AssertionError("expected " + type.getSimpleName() + " but nothing was thrown");
    }

    /** Stand-in for one checked-out kernel license session. */
    static final class Session {
        final int serial;
        Session(int serial) { this.serial = serial; }
        @Override public String toString() { return "S" + serial; }
    }

    /** Deterministic factory + retire log used by most tests. */
    static final class Rig {
        final AtomicInteger serials = new AtomicInteger();
        final List<Session> retiredLog = new ArrayList<>();
        final LeasePool<Session> pool;

        Rig(int maxSize) {
            pool = new LeasePool<>(maxSize,
                    () -> new Session(serials.incrementAndGet()),
                    retiredLog::add);
        }

        List<Integer> retiredSerials() {
            List<Integer> out = new ArrayList<>();
            for (Session s : retiredLog) out.add(s.serial);
            return out;
        }
    }

    public static void main(String[] args) {

        test("constructor_validation", () -> {
            eq("maxSize message", "maxSize must be >= 1",
                    thrown(IllegalArgumentException.class,
                            () -> new LeasePool<Session>(0, () -> new Session(1), s -> {})).getMessage());
            eq("factory message", "factory must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new LeasePool<Session>(1, null, s -> {})).getMessage());
            eq("retire message", "retire must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new LeasePool<Session>(1, () -> new Session(1), null)).getMessage());
        });

        test("acquire_creates_lazily_and_accounts", () -> {
            Rig rig = new Rig(3);
            eq("nothing created up front", 0, rig.pool.createdCount());
            Lease<Session> lease = rig.pool.acquire();
            eq("lease ids start at 1", 1, lease.id());
            eq("session serial", 1, lease.get().serial);
            yes("lease active", lease.active());
            eq("created", 1, rig.pool.createdCount());
            eq("outstanding", 1, rig.pool.outstandingCount());
            eq("idle", 0, rig.pool.idleCount());
        });

        test("try_with_resources_returns_the_session", () -> {
            Rig rig = new Rig(2);
            Lease<Session> observed;
            try (Lease<Session> lease = rig.pool.acquire()) {
                observed = lease;
                eq("in use inside the block", 1, rig.pool.outstandingCount());
            }
            yes("inactive after the block", !observed.active());
            eq("outstanding drained", 0, rig.pool.outstandingCount());
            eq("session parked idle", 1, rig.pool.idleCount());
            eq("nothing retired by a plain return", List.of(), rig.retiredSerials());
        });

        test("reuse_is_lifo_most_recently_returned_first", () -> {
            Rig rig = new Rig(3);
            Lease<Session> a = rig.pool.acquire();
            Lease<Session> b = rig.pool.acquire();
            Session s1 = a.get();
            Session s2 = b.get();
            a.close();
            b.close();
            Lease<Session> c = rig.pool.acquire();
            yes("last returned comes back first", c.get() == s2);
            eq("fresh lease id even on reuse", 3, c.id());
            Lease<Session> d = rig.pool.acquire();
            yes("then the earlier one", d.get() == s1);
            eq("no extra sessions were created", 2, rig.pool.createdCount());
        });

        test("exhaustion_throws_and_recovers", () -> {
            Rig rig = new Rig(2);
            Lease<Session> a = rig.pool.acquire();
            Lease<Session> b = rig.pool.acquire();
            Session s2 = b.get();
            PoolExhaustedException e = thrown(PoolExhaustedException.class, () -> rig.pool.acquire());
            eq("message", "pool exhausted: all 2 permits leased", e.getMessage());
            b.close();
            Lease<Session> c = rig.pool.acquire();
            yes("freed seat reused", c.get() == s2);
            eq("still only two sessions ever", 2, rig.pool.createdCount());
            yes("a untouched by the failed acquire", a.active());
        });

        test("double_close_of_a_lease_is_a_noop", () -> {
            Rig rig = new Rig(3);
            Lease<Session> a = rig.pool.acquire();
            Session s1 = a.get();
            a.close();
            a.close();
            eq("closed twice, parked once", 1, rig.pool.idleCount());
            Lease<Session> b = rig.pool.acquire();
            yes("first reacquire gets the parked session", b.get() == s1);
            Lease<Session> c = rig.pool.acquire();
            eq("second reacquire must CREATE (no phantom idle entry)", 2, rig.pool.createdCount());
            yes("and it is a different session", c.get() != s1);
        });

        test("use_after_close_is_rejected", () -> {
            Rig rig = new Rig(1);
            Lease<Session> a = rig.pool.acquire();
            a.close();
            IllegalStateException e = thrown(IllegalStateException.class, () -> a.get());
            eq("message", "lease 1 is closed", e.getMessage());
            yes("inactive", !a.active());
        });

        test("discard_retires_broken_session_and_frees_capacity", () -> {
            Rig rig = new Rig(1);
            Lease<Session> a = rig.pool.acquire();
            Session s1 = a.get();
            a.discard();
            eq("retired immediately", List.of(1), rig.retiredSerials());
            yes("lease dead", !a.active());
            eq("message", "lease 1 was discarded",
                    thrown(IllegalStateException.class, () -> a.get()).getMessage());
            a.close();
            eq("close after discard returns nothing to the pool", 0, rig.pool.idleCount());
            eq("retire not called again", List.of(1), rig.retiredSerials());
            Lease<Session> b = rig.pool.acquire();
            yes("capacity freed for a fresh session", b.get() != s1);
            eq("created a replacement", 2, rig.pool.createdCount());
        });

        test("discard_after_close_is_a_noop", () -> {
            Rig rig = new Rig(1);
            Lease<Session> a = rig.pool.acquire();
            a.close();
            a.discard();
            eq("session stays parked", 1, rig.pool.idleCount());
            eq("nothing retired", List.of(), rig.retiredSerials());
        });

        test("factory_returning_null_is_an_error", () -> {
            List<Session> log = new ArrayList<>();
            LeasePool<Session> pool = new LeasePool<>(1, () -> null, log::add);
            IllegalStateException e = thrown(IllegalStateException.class, () -> pool.acquire());
            eq("message", "factory returned null", e.getMessage());
            eq("nothing counted as created", 0, pool.createdCount());
            eq("nothing outstanding", 0, pool.outstandingCount());
        });

        test("factory_exception_leaves_pool_consistent", () -> {
            AtomicInteger calls = new AtomicInteger();
            List<Session> log = new ArrayList<>();
            LeasePool<Session> pool = new LeasePool<>(2, () -> {
                if (calls.incrementAndGet() == 1) {
                    throw new IllegalStateException("license server down");
                }
                return new Session(calls.get());
            }, log::add);
            eq("factory failure propagates", "license server down",
                    thrown(IllegalStateException.class, () -> pool.acquire()).getMessage());
            eq("failed acquire created nothing", 0, pool.createdCount());
            eq("failed acquire leased nothing", 0, pool.outstandingCount());
            Lease<Session> a = pool.acquire();
            eq("failed attempts do not burn lease ids", 1, a.id());
            eq("created on retry", 1, pool.createdCount());
        });

        test("outstanding_ids_are_the_leak_report", () -> {
            Rig rig = new Rig(5);
            Lease<Session> a = rig.pool.acquire();
            Lease<Session> b = rig.pool.acquire();
            Lease<Session> c = rig.pool.acquire();
            b.close();
            eq("ascending leaked ids", List.of(1, 3), rig.pool.outstandingIds());
            eq("outstanding count", 2, rig.pool.outstandingCount());
            thrown(UnsupportedOperationException.class, () -> rig.pool.outstandingIds().add(9));
            yes("a and c still usable", a.active() && c.active());
        });

        test("pool_close_annuls_leases_and_retires_everything_in_order", () -> {
            Rig rig = new Rig(4);
            Lease<Session> a = rig.pool.acquire();   // S1, id 1 — stays leased
            Lease<Session> b = rig.pool.acquire();   // S2, id 2 — returned
            Lease<Session> c = rig.pool.acquire();   // S3, id 3 — stays leased
            b.close();
            rig.pool.close();
            eq("idle retired first (LIFO), then outstanding by lease id",
                    List.of(2, 1, 3), rig.retiredSerials());
            yes("outstanding leases annulled", !a.active() && !c.active());
            eq("annulled message", "lease 1 was annulled by pool close",
                    thrown(IllegalStateException.class, () -> a.get()).getMessage());
            a.close();
            eq("closing an annulled lease retires nothing extra", List.of(2, 1, 3), rig.retiredSerials());
            eq("idle drained", 0, rig.pool.idleCount());
            eq("outstanding drained", 0, rig.pool.outstandingCount());
            eq("leak report empty after close", List.of(), rig.pool.outstandingIds());
            eq("conservation: retired == created", rig.pool.createdCount(), rig.pool.retiredCount());
        });

        test("acquire_after_close_is_rejected", () -> {
            Rig rig = new Rig(1);
            rig.pool.close();
            eq("message", "pool is closed",
                    thrown(IllegalStateException.class, () -> rig.pool.acquire()).getMessage());
        });

        test("double_pool_close_is_a_noop", () -> {
            Rig rig = new Rig(2);
            Lease<Session> a = rig.pool.acquire();
            rig.pool.close();
            eq("first close retired the leased session", List.of(1), rig.retiredSerials());
            rig.pool.close();
            eq("second close retires nothing more", List.of(1), rig.retiredSerials());
            eq("retiredCount stable", 1, rig.pool.retiredCount());
            yes("lease stays annulled", !a.active());
        });

        test("pool_is_auto_closeable_in_try_with_resources", () -> {
            List<Session> log = new ArrayList<>();
            AtomicInteger serials = new AtomicInteger();
            Lease<Session> leaked;
            try (LeasePool<Session> pool = new LeasePool<>(2,
                    () -> new Session(serials.incrementAndGet()), log::add)) {
                leaked = pool.acquire();
                eq("leak visible before close", List.of(1), pool.outstandingIds());
            }
            yes("try-with-resources annulled the leak", !leaked.active());
            eq("and retired its session", 1, log.size());
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
