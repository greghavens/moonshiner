import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class TestMain {
    private static int failures;

    @FunctionalInterface
    private interface CheckedTest {
        void run() throws Exception;
    }

    public static void main(String[] args) {
        run("revocation hands off the completed checkpoint", TestMain::checkpointHandoff);
        run("new owner does not process a handed-off record twice", TestMain::noDuplicateAfterRebalance);
        run("old owner cannot leak a revoked commit", TestMain::noCommitAfterRevocation);
        run("revoked member ignores stale fetched records", TestMain::staleRecordIgnored);
        run("ordinary periodic commits still resume correctly", TestMain::ordinaryCommit);

        if (failures != 0) {
            System.err.println("FAILED: " + failures + " test(s)");
            System.exit(1);
        }
        System.out.println("ALL PASS");
    }

    private static void checkpointHandoff() {
        Fixture fixture = new Fixture();
        fixture.group.rebalance(assignments(
                "alpha", set(fixture.orders0),
                "beta", Set.of()));
        fixture.alpha.process(List.of(record(fixture.orders0, 0, "order-100")));

        fixture.group.rebalance(assignments(
                "alpha", Set.of(),
                "beta", set(fixture.orders0)));

        equal(1L, fixture.group.committed(fixture.orders0),
                "revocation must publish the next offset before transfer");
        equal(
                List.of(new SimulatedConsumerGroup.CommitAttempt(
                        "alpha", fixture.orders0, 1L, true, "alpha")),
                fixture.group.commitAttempts(),
                "the handoff commit must happen while alpha is coordinator owner");
    }

    private static void noDuplicateAfterRebalance() {
        Fixture fixture = new Fixture();
        fixture.group.rebalance(assignments(
                "alpha", set(fixture.orders0),
                "beta", Set.of()));
        fixture.alpha.process(List.of(record(fixture.orders0, 0, "order-100")));
        fixture.group.rebalance(assignments(
                "alpha", Set.of(),
                "beta", set(fixture.orders0)));

        fixture.beta.process(List.of(
                record(fixture.orders0, 0, "order-100-redelivery"),
                record(fixture.orders0, 1, "order-101")));

        equal(
                List.of("alpha:orders-0@0", "beta:orders-0@1"),
                fixture.processed,
                "the new owner must skip the offset completed before rebalance");
    }

    private static void noCommitAfterRevocation() {
        Fixture fixture = new Fixture();
        fixture.group.rebalance(assignments(
                "alpha", set(fixture.orders0, fixture.orders1),
                "beta", Set.of()));
        fixture.alpha.process(List.of(
                record(fixture.orders0, 0, "order-100"),
                record(fixture.orders1, 0, "order-200")));

        fixture.group.rebalance(assignments(
                "alpha", set(fixture.orders1),
                "beta", set(fixture.orders0)));
        fixture.group.clearCommitAttempts();

        fixture.alpha.commitPending();

        equal(
                List.of(new SimulatedConsumerGroup.CommitAttempt(
                        "alpha", fixture.orders1, 1L, true, "alpha")),
                fixture.group.commitAttempts(),
                "the later periodic commit must contain only the retained partition");
        equal(1L, fixture.group.committed(fixture.orders0),
                "the moved partition checkpoint must remain at its handoff value");
        equal(1L, fixture.group.committed(fixture.orders1),
                "the retained partition must commit normally");
    }

    private static void staleRecordIgnored() {
        Fixture fixture = new Fixture();
        fixture.group.rebalance(assignments(
                "alpha", set(fixture.orders0),
                "beta", Set.of()));
        fixture.alpha.process(List.of(record(fixture.orders0, 0, "order-100")));
        fixture.group.rebalance(assignments(
                "alpha", Set.of(),
                "beta", set(fixture.orders0)));

        fixture.alpha.process(List.of(record(fixture.orders0, 1, "stale-poll")));

        equal(List.of("alpha:orders-0@0"), fixture.processed,
                "a revoked member must ignore a record left in its old poll batch");
        equal(List.of(), fixture.alpha.pendingPartitions(),
                "revoked checkpoint state must be gone after handoff");
    }

    private static void ordinaryCommit() {
        Fixture fixture = new Fixture();
        fixture.group.rebalance(assignments(
                "alpha", set(fixture.orders1),
                "beta", Set.of()));
        fixture.alpha.process(List.of(record(fixture.orders1, 0, "order-200")));
        fixture.alpha.commitPending();
        fixture.alpha.commitPending();

        equal(1L, fixture.group.committed(fixture.orders1),
                "periodic commit must store the next offset");
        equal(1, fixture.group.commitAttempts().size(),
                "a cleared pending checkpoint must not be committed twice");
        equal(true, fixture.group.commitAttempts().get(0).accepted(),
                "ordinary commit must be made by the current owner");
    }

    private static StreamRecord record(TopicPartition partition, long offset, String value) {
        return new StreamRecord(partition, offset, value);
    }

    @SafeVarargs
    private static <T> Set<T> set(T... values) {
        Set<T> result = new LinkedHashSet<>();
        for (T value : values) {
            result.add(value);
        }
        return result;
    }

    private static Map<String, Set<TopicPartition>> assignments(
            String firstMember,
            Set<TopicPartition> first,
            String secondMember,
            Set<TopicPartition> second) {
        Map<String, Set<TopicPartition>> result = new LinkedHashMap<>();
        result.put(firstMember, first);
        result.put(secondMember, second);
        return result;
    }

    private static void run(String name, CheckedTest test) {
        try {
            test.run();
            System.out.println("PASS " + name);
        } catch (Throwable failure) {
            failures++;
            System.err.println("FAIL " + name + ": " + failure.getMessage());
        }
    }

    private static void equal(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(
                    message + "\n  expected: " + expected + "\n    actual: " + actual);
        }
    }

    private static final class Fixture {
        final SimulatedConsumerGroup group = new SimulatedConsumerGroup();
        final TopicPartition orders0 = new TopicPartition("orders", 0);
        final TopicPartition orders1 = new TopicPartition("orders", 1);
        final List<String> processed = new ArrayList<>();
        final StreamProcessor alpha = processor("alpha");
        final StreamProcessor beta = processor("beta");

        Fixture() {
            group.register(alpha);
            group.register(beta);
        }

        private StreamProcessor processor(String memberId) {
            return new StreamProcessor(memberId, group, (member, record) ->
                    processed.add(member + ":" + record.partition() + "@" + record.offset()));
        }
    }
}
