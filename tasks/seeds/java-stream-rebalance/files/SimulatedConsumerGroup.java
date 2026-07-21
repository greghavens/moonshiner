import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.NavigableMap;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * Deterministic consumer-group coordinator and checkpoint store used by the
 * acceptance contract. There are no threads, sleeps, brokers, or network calls.
 */
public final class SimulatedConsumerGroup implements CheckpointStore {
    public record CommitAttempt(
            String memberId,
            TopicPartition partition,
            long nextOffset,
            boolean accepted,
            String ownerAtAttempt) {}

    private final Map<String, StreamProcessor> members = new LinkedHashMap<>();
    private final NavigableMap<TopicPartition, String> owners = new TreeMap<>();
    private final NavigableMap<TopicPartition, Long> committed = new TreeMap<>();
    private final List<CommitAttempt> attempts = new ArrayList<>();

    public void register(StreamProcessor member) {
        Objects.requireNonNull(member, "member");
        StreamProcessor previous = members.putIfAbsent(member.memberId(), member);
        if (previous != null) {
            throw new IllegalArgumentException("duplicate member " + member.memberId());
        }
    }

    /**
     * Runs a rebalance in listener order: revoke while old ownership is live,
     * atomically install target ownership, then notify newly assigned members.
     */
    public void rebalance(Map<String, Set<TopicPartition>> targetAssignments) {
        Objects.requireNonNull(targetAssignments, "targetAssignments");
        for (String memberId : targetAssignments.keySet()) {
            if (!members.containsKey(memberId)) {
                throw new IllegalArgumentException("unknown member " + memberId);
            }
        }

        NavigableMap<TopicPartition, String> targetOwners = new TreeMap<>();
        for (Map.Entry<String, Set<TopicPartition>> entry : targetAssignments.entrySet()) {
            for (TopicPartition partition : entry.getValue()) {
                String previous = targetOwners.putIfAbsent(partition, entry.getKey());
                if (previous != null) {
                    throw new IllegalArgumentException(
                            partition + " assigned to both " + previous + " and " + entry.getKey());
                }
            }
        }

        Map<String, Set<TopicPartition>> before = assignmentsByMember(owners);
        Map<String, Set<TopicPartition>> after = assignmentsByMember(targetOwners);

        for (Map.Entry<String, StreamProcessor> entry : members.entrySet()) {
            Set<TopicPartition> revoked = new TreeSet<>(before.get(entry.getKey()));
            revoked.removeAll(after.get(entry.getKey()));
            if (!revoked.isEmpty()) {
                entry.getValue().onPartitionsRevoked(revoked);
            }
        }

        owners.clear();
        owners.putAll(targetOwners);

        for (Map.Entry<String, StreamProcessor> entry : members.entrySet()) {
            Set<TopicPartition> assigned = new TreeSet<>(after.get(entry.getKey()));
            assigned.removeAll(before.get(entry.getKey()));
            if (!assigned.isEmpty()) {
                entry.getValue().onPartitionsAssigned(assigned);
            }
        }
    }

    private Map<String, Set<TopicPartition>> assignmentsByMember(
            Map<TopicPartition, String> assignment) {
        Map<String, Set<TopicPartition>> result = new LinkedHashMap<>();
        for (String memberId : members.keySet()) {
            result.put(memberId, new LinkedHashSet<>());
        }
        for (Map.Entry<TopicPartition, String> entry : assignment.entrySet()) {
            result.get(entry.getValue()).add(entry.getKey());
        }
        return result;
    }

    @Override
    public long committed(TopicPartition partition) {
        return committed.getOrDefault(partition, 0L);
    }

    @Override
    public void commit(String memberId, TopicPartition partition, long nextOffset) {
        String currentOwner = owners.get(partition);
        boolean accepted = Objects.equals(memberId, currentOwner);
        attempts.add(new CommitAttempt(
                memberId, partition, nextOffset, accepted, currentOwner));
        if (!accepted) {
            throw new IllegalStateException(
                    memberId + " cannot commit " + partition
                            + "; owner is " + String.valueOf(currentOwner));
        }
        if (nextOffset < committed(partition)) {
            throw new IllegalArgumentException("checkpoint cannot move backwards");
        }
        committed.put(partition, nextOffset);
    }

    public List<CommitAttempt> commitAttempts() {
        return Collections.unmodifiableList(new ArrayList<>(attempts));
    }

    public void clearCommitAttempts() {
        attempts.clear();
    }
}
