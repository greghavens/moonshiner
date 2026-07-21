import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.NavigableMap;
import java.util.NavigableSet;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.BiConsumer;

/**
 * A synchronous stream member with batched, partition-scoped checkpoints.
 *
 * <p>The coordinator owns assignment changes; this class owns the local
 * processing position and the pending next offset for each assigned partition.
 */
public final class StreamProcessor {
    private final String memberId;
    private final CheckpointStore checkpoints;
    private final BiConsumer<String, StreamRecord> handler;
    private final NavigableSet<TopicPartition> assigned = new TreeSet<>();
    private final NavigableMap<TopicPartition, Long> nextOffsets = new TreeMap<>();
    private final NavigableMap<TopicPartition, Long> pending = new TreeMap<>();

    public StreamProcessor(
            String memberId,
            CheckpointStore checkpoints,
            BiConsumer<String, StreamRecord> handler) {
        this.memberId = Objects.requireNonNull(memberId, "memberId");
        this.checkpoints = Objects.requireNonNull(checkpoints, "checkpoints");
        this.handler = Objects.requireNonNull(handler, "handler");
        if (memberId.isBlank()) {
            throw new IllegalArgumentException("memberId must not be blank");
        }
    }

    public String memberId() {
        return memberId;
    }

    /** Called after coordinator ownership has been installed for new partitions. */
    public void onPartitionsAssigned(Set<TopicPartition> partitions) {
        for (TopicPartition partition : new TreeSet<>(partitions)) {
            if (assigned.add(partition)) {
                nextOffsets.put(partition, checkpoints.committed(partition));
            }
        }
    }

    /** Called while this member is still coordinator owner of revoked partitions. */
    public void onPartitionsRevoked(Set<TopicPartition> partitions) {
        for (TopicPartition partition : new TreeSet<>(partitions)) {
            assigned.remove(partition);
            nextOffsets.remove(partition);
        }
    }

    /**
     * Processes assigned records in delivery order. Already-checkpointed records
     * are skipped; a forward gap is rejected because this simulation never seeks.
     */
    public void process(List<StreamRecord> records) {
        for (StreamRecord record : records) {
            TopicPartition partition = record.partition();
            if (!assigned.contains(partition)) {
                continue;
            }

            long expected = nextOffsets.get(partition);
            if (record.offset() < expected) {
                continue;
            }
            if (record.offset() > expected) {
                throw new IllegalStateException(
                        "gap for " + partition + ": expected " + expected
                                + " but got " + record.offset());
            }

            handler.accept(memberId, record);
            long nextOffset = Math.addExact(record.offset(), 1L);
            nextOffsets.put(partition, nextOffset);
            pending.put(partition, nextOffset);
        }
    }

    /** Commits all pending progress, normally from the periodic commit loop. */
    public void commitPending() {
        commitPartitions(new TreeSet<>(pending.keySet()));
    }

    private void commitPartitions(Collection<TopicPartition> partitions) {
        for (TopicPartition partition : new TreeSet<>(partitions)) {
            Long nextOffset = pending.get(partition);
            if (nextOffset == null) {
                continue;
            }
            checkpoints.commit(memberId, partition, nextOffset);
            pending.remove(partition, nextOffset);
        }
    }

    public Set<TopicPartition> assignedPartitions() {
        return Collections.unmodifiableSet(new TreeSet<>(assigned));
    }

    public List<TopicPartition> pendingPartitions() {
        return Collections.unmodifiableList(new ArrayList<>(pending.keySet()));
    }
}
