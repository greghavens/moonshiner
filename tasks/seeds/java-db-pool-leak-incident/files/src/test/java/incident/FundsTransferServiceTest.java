package incident;

import incident.FundsTransferService.DatabaseConnection;
import incident.FundsTransferService.DatabaseException;
import incident.FundsTransferService.Transfer;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

public final class FundsTransferServiceTest {
    private static final Transfer RENT = new Transfer("cash", "rent", 2_500L);

    private FundsTransferServiceTest() {
    }

    public static void main(String[] args) throws Exception {
        successfulTransferCommitsAndReturnsConnection();
        failedCreditRollsBackReturnsConnectionAndLeavesCapacityReusable();
        cleanupFailuresRemainSuppressedOnTheOperationFailure();
        closeFailureAfterCommitRemainsTheReportedFailure();
        validationDoesNotBorrowAConnection();
        System.out.println("FundsTransferServiceTest: PASS");
    }

    private static void successfulTransferCommitsAndReturnsConnection()
            throws Exception {
        TrackingPool pool = TrackingPool.healthy();
        FundsTransferService service = new FundsTransferService(pool);

        service.transfer("req-ok", RENT);

        assertEquals(7_500L, pool.balance("cash"), "committed debit balance");
        assertEquals(2_500L, pool.balance("rent"), "committed credit balance");
        assertEquals(
                new PoolMetrics(1, 0, 1, 1, 1),
                pool.metrics(),
                "success pool metrics");
        assertEquals(
                List.of(
                        "request=req-ok checkout=db-1 active=1 idle=0",
                        "request=req-ok tx=begin",
                        "request=req-ok statement=debit account=cash cents=2500 outcome=ok",
                        "request=req-ok statement=credit account=rent cents=2500 outcome=ok",
                        "request=req-ok tx=commit",
                        "request=req-ok checkin=db-1 active=0 idle=1"),
                pool.trace(),
                "successful request trace");
    }

    private static void failedCreditRollsBackReturnsConnectionAndLeavesCapacityReusable()
            throws Exception {
        DatabaseException creditFailure = new DatabaseException("ledger-write-failed");
        TrackingPool pool = new TrackingPool(creditFailure, null, null);
        FundsTransferService service = new FundsTransferService(pool);

        DatabaseException actual = expectDatabaseException(
                () -> service.transfer("req-fail", RENT));

        assertSame(creditFailure, actual, "operation failure identity");
        assertEquals(List.of(), List.of(actual.getSuppressed()),
                "operation failure suppressed exceptions");
        assertEquals(10_000L, pool.balance("cash"), "rolled-back debit balance");
        assertEquals(0L, pool.balance("rent"), "rolled-back credit balance");
        assertEquals(
                new PoolMetrics(1, 0, 1, 1, 1),
                pool.metrics(),
                "failed request must not retain the connection");
        assertEquals(
                List.of(
                        "request=req-fail checkout=db-1 active=1 idle=0",
                        "request=req-fail tx=begin",
                        "request=req-fail statement=debit account=cash cents=2500 outcome=ok",
                        "request=req-fail statement=credit account=rent cents=2500 outcome=error error=ledger-write-failed",
                        "request=req-fail tx=rollback",
                        "request=req-fail checkin=db-1 active=0 idle=1"),
                pool.trace(),
                "failed request trace must end in check-in");

        pool.clearCreditFailure();
        service.transfer("req-recovery", new Transfer("cash", "rent", 1_000L));
        assertEquals(
                new PoolMetrics(1, 0, 1, 2, 2),
                pool.metrics(),
                "a later request reuses the sole pool slot");
        assertEquals(9_000L, pool.balance("cash"), "recovery debit balance");
        assertEquals(1_000L, pool.balance("rent"), "recovery credit balance");
    }

    private static void cleanupFailuresRemainSuppressedOnTheOperationFailure()
            throws Exception {
        DatabaseException creditFailure = new DatabaseException("credit-primary");
        DatabaseException rollbackFailure = new DatabaseException("rollback-cleanup");
        DatabaseException closeFailure = new DatabaseException("close-cleanup");
        TrackingPool pool = new TrackingPool(
                creditFailure, rollbackFailure, closeFailure);
        FundsTransferService service = new FundsTransferService(pool);

        DatabaseException actual = expectDatabaseException(
                () -> service.transfer("req-cleanup", RENT));

        assertSame(creditFailure, actual, "primary transaction failure identity");
        assertIdentityList(
                List.of(rollbackFailure, closeFailure),
                List.of(actual.getSuppressed()),
                "cleanup failures in causal order");
        assertEquals(
                List.of(
                        "request=req-cleanup checkout=db-1 active=1 idle=0",
                        "request=req-cleanup tx=begin",
                        "request=req-cleanup statement=debit account=cash cents=2500 outcome=ok",
                        "request=req-cleanup statement=credit account=rent cents=2500 outcome=error error=credit-primary",
                        "request=req-cleanup tx=rollback outcome=error error=rollback-cleanup",
                        "request=req-cleanup checkin=db-1 outcome=error error=close-cleanup"),
                pool.trace(),
                "transaction and ownership cleanup order");
    }

    private static void closeFailureAfterCommitRemainsTheReportedFailure()
            throws Exception {
        DatabaseException closeFailure = new DatabaseException("close-after-commit");
        TrackingPool pool = new TrackingPool(null, null, closeFailure);
        FundsTransferService service = new FundsTransferService(pool);

        DatabaseException actual = expectDatabaseException(
                () -> service.transfer("req-close", RENT));

        assertSame(closeFailure, actual, "successful transaction close failure identity");
        assertEquals(List.of(), List.of(actual.getSuppressed()),
                "successful transaction close failure suppression");
        assertEquals(7_500L, pool.balance("cash"), "committed debit before close failure");
        assertEquals(2_500L, pool.balance("rent"), "committed credit before close failure");
        assertEquals(
                List.of(
                        "request=req-close checkout=db-1 active=1 idle=0",
                        "request=req-close tx=begin",
                        "request=req-close statement=debit account=cash cents=2500 outcome=ok",
                        "request=req-close statement=credit account=rent cents=2500 outcome=ok",
                        "request=req-close tx=commit",
                        "request=req-close checkin=db-1 outcome=error error=close-after-commit"),
                pool.trace(),
                "successful transaction close failure trace");
    }

    private static void validationDoesNotBorrowAConnection() throws Exception {
        TrackingPool pool = TrackingPool.healthy();
        FundsTransferService service = new FundsTransferService(pool);

        IllegalArgumentException failure = expectIllegalArgument(
                () -> service.transfer(" ", RENT));

        assertEquals("requestId must not be blank", failure.getMessage(),
                "request id validation message");
        assertEquals(
                new PoolMetrics(1, 0, 1, 0, 0),
                pool.metrics(),
                "validation failure pool metrics");
        assertEquals(List.of(), pool.trace(), "validation failure trace");
    }

    @FunctionalInterface
    private interface CheckedAction {
        void run() throws Exception;
    }

    private static DatabaseException expectDatabaseException(CheckedAction action)
            throws Exception {
        try {
            action.run();
        } catch (DatabaseException expected) {
            return expected;
        }
        throw new AssertionError("expected DatabaseException");
    }

    private static IllegalArgumentException expectIllegalArgument(CheckedAction action)
            throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return expected;
        }
        throw new AssertionError("expected IllegalArgumentException");
    }

    private static void assertIdentityList(
            List<?> expected, List<?> actual, String label) {
        if (expected.size() != actual.size()) {
            throw new AssertionError(
                    label + ": expected " + expected.size() + " entries but saw "
                            + actual.size());
        }
        for (int index = 0; index < expected.size(); index++) {
            assertSame(expected.get(index), actual.get(index), label + "[" + index + "]");
        }
    }

    private static void assertSame(Object expected, Object actual, String label) {
        if (expected != actual) {
            throw new AssertionError(label + ": expected the original object");
        }
    }

    private static void assertEquals(Object expected, Object actual, String label) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(
                    label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private record PoolMetrics(
            int capacity, int active, int idle, int checkouts, int checkins) {
    }

    private static final class TrackingPool
            implements FundsTransferService.ConnectionPool {
        private final Map<String, Long> committed = new LinkedHashMap<>();
        private final List<String> trace = new ArrayList<>();
        private DatabaseException creditFailure;
        private final DatabaseException rollbackFailure;
        private final DatabaseException closeFailure;
        private boolean available = true;
        private int checkouts;
        private int checkins;

        private TrackingPool(
                DatabaseException creditFailure,
                DatabaseException rollbackFailure,
                DatabaseException closeFailure) {
            this.creditFailure = creditFailure;
            this.rollbackFailure = rollbackFailure;
            this.closeFailure = closeFailure;
            committed.put("cash", 10_000L);
            committed.put("rent", 0L);
        }

        private static TrackingPool healthy() {
            return new TrackingPool(null, null, null);
        }

        @Override
        public DatabaseConnection borrow(String requestId) throws DatabaseException {
            if (!available) {
                trace.add("request=" + requestId
                        + " checkout=blocked active=1 idle=0");
                throw new DatabaseException("pool-exhausted");
            }
            available = false;
            checkouts++;
            trace.add("request=" + requestId + " checkout=db-1 active=1 idle=0");
            return new TrackingConnection(requestId);
        }

        private PoolMetrics metrics() {
            return new PoolMetrics(1, available ? 0 : 1, available ? 1 : 0,
                    checkouts, checkins);
        }

        private List<String> trace() {
            return List.copyOf(trace);
        }

        private long balance(String account) {
            return committed.get(account);
        }

        private void clearCreditFailure() {
            creditFailure = null;
        }

        private final class TrackingConnection implements DatabaseConnection {
            private final String requestId;
            private Map<String, Long> pending;
            private boolean closed;

            private TrackingConnection(String requestId) {
                this.requestId = requestId;
            }

            @Override
            public void begin() {
                requireOpen();
                pending = new LinkedHashMap<>(committed);
                trace.add("request=" + requestId + " tx=begin");
            }

            @Override
            public void debit(String account, long cents) throws DatabaseException {
                requireTransaction();
                long balance = pending.getOrDefault(account, 0L);
                if (balance < cents) {
                    throw new DatabaseException("insufficient-funds");
                }
                pending.put(account, balance - cents);
                trace.add("request=" + requestId + " statement=debit account="
                        + account + " cents=" + cents + " outcome=ok");
            }

            @Override
            public void credit(String account, long cents) throws DatabaseException {
                requireTransaction();
                if (creditFailure != null) {
                    trace.add("request=" + requestId + " statement=credit account="
                            + account + " cents=" + cents + " outcome=error error="
                            + creditFailure.getMessage());
                    throw creditFailure;
                }
                pending.put(account, pending.getOrDefault(account, 0L) + cents);
                trace.add("request=" + requestId + " statement=credit account="
                        + account + " cents=" + cents + " outcome=ok");
            }

            @Override
            public void commit() {
                requireTransaction();
                committed.clear();
                committed.putAll(pending);
                pending = null;
                trace.add("request=" + requestId + " tx=commit");
            }

            @Override
            public void rollback() throws DatabaseException {
                requireOpen();
                if (rollbackFailure != null) {
                    trace.add("request=" + requestId
                            + " tx=rollback outcome=error error="
                            + rollbackFailure.getMessage());
                    throw rollbackFailure;
                }
                pending = null;
                trace.add("request=" + requestId + " tx=rollback");
            }

            @Override
            public void close() throws DatabaseException {
                if (closed) {
                    throw new AssertionError("connection was closed more than once");
                }
                closed = true;
                if (closeFailure != null) {
                    trace.add("request=" + requestId
                            + " checkin=db-1 outcome=error error="
                            + closeFailure.getMessage());
                    throw closeFailure;
                }
                available = true;
                checkins++;
                trace.add("request=" + requestId
                        + " checkin=db-1 active=0 idle=1");
            }

            private void requireOpen() {
                if (closed) {
                    throw new IllegalStateException("connection is closed");
                }
            }

            private void requireTransaction() {
                requireOpen();
                if (pending == null) {
                    throw new IllegalStateException("no active transaction");
                }
            }
        }
    }
}
