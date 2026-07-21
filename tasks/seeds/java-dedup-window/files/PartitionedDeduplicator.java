import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.OptionalLong;
import java.util.Set;

/**
 * A thread-safe, bounded replay guard for streams whose sequence numbers are
 * assigned independently per partition.
 */
public final class PartitionedDeduplicator {
    private final int windowSize;
    private final Map<String, Long> highWatermarks = new HashMap<>();
    private final Set<EventPosition> retained = new HashSet<>();

    public PartitionedDeduplicator(int windowSize) {
        if (windowSize < 1) {
            throw new IllegalArgumentException("windowSize must be >= 1");
        }
        this.windowSize = windowSize;
    }

    public synchronized DedupDecision observe(String partition, long sequence) {
        validatePosition(partition, sequence);

        Long previousHigh = highWatermarks.get(partition);
        if (previousHigh == null || sequence > previousHigh) {
            highWatermarks.put(partition, sequence);
            long minimum = minimumRetained(sequence);
            retained.removeIf(position -> position.partition().equals(partition)
                    && position.sequence() < minimum);
        } else if (sequence < minimumRetained(previousHigh)) {
            return DedupDecision.TOO_OLD;
        }

        EventPosition position = new EventPosition(partition, sequence);
        return retained.add(position) ? DedupDecision.ACCEPTED : DedupDecision.DUPLICATE;
    }

    public synchronized String checkpoint() {
        return DedupCheckpoint.encode(windowSize, highWatermarks, retained);
    }

    public static PartitionedDeduplicator restore(String checkpoint) {
        DedupCheckpoint.State state = DedupCheckpoint.decode(checkpoint);
        PartitionedDeduplicator deduplicator =
                new PartitionedDeduplicator(state.windowSize());
        deduplicator.highWatermarks.putAll(state.highWatermarks());
        deduplicator.retained.addAll(state.retained());
        return deduplicator;
    }

    public int windowSize() {
        return windowSize;
    }

    public synchronized int partitionCount() {
        return highWatermarks.size();
    }

    public synchronized int trackedEventCount() {
        return retained.size();
    }

    public synchronized int trackedEventCount(String partition) {
        validatePartition(partition);
        return (int) retained.stream()
                .filter(position -> position.partition().equals(partition))
                .count();
    }

    public synchronized OptionalLong highWatermark(String partition) {
        validatePartition(partition);
        Long value = highWatermarks.get(partition);
        return value == null ? OptionalLong.empty() : OptionalLong.of(value);
    }

    private long minimumRetained(long highWatermark) {
        return Math.max(0L, highWatermark - (long) windowSize + 1L);
    }

    private static void validatePosition(String partition, long sequence) {
        validatePartition(partition);
        if (sequence < 0) {
            throw new IllegalArgumentException("sequence must be >= 0");
        }
    }

    private static void validatePartition(String partition) {
        if (partition == null || partition.isEmpty()) {
            throw new IllegalArgumentException("partition must not be empty");
        }
    }
}
