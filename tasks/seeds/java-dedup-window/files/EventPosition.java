import java.util.Objects;

/** Immutable identity of an event in the input stream. */
final class EventPosition {
    private final String partition;
    private final long sequence;

    EventPosition(String partition, long sequence) {
        this.partition = Objects.requireNonNull(partition, "partition");
        this.sequence = sequence;
    }

    String partition() {
        return partition;
    }

    long sequence() {
        return sequence;
    }

    @Override
    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof EventPosition that)) {
            return false;
        }
        return sequence == that.sequence;
    }

    @Override
    public int hashCode() {
        return Long.hashCode(sequence);
    }

    @Override
    public String toString() {
        return partition + "@" + sequence;
    }
}
