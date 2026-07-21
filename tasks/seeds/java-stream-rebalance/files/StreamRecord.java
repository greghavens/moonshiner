import java.util.Objects;

/** One deterministic input record. Offsets are zero-based. */
public record StreamRecord(TopicPartition partition, long offset, String value) {
    public StreamRecord {
        Objects.requireNonNull(partition, "partition");
        Objects.requireNonNull(value, "value");
        if (offset < 0) {
            throw new IllegalArgumentException("offset must be >= 0");
        }
    }
}
