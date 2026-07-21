import java.util.Objects;

/** Stable identity for one partition in the simulated stream. */
public record TopicPartition(String topic, int partition)
        implements Comparable<TopicPartition> {

    public TopicPartition {
        Objects.requireNonNull(topic, "topic");
        if (topic.isBlank()) {
            throw new IllegalArgumentException("topic must not be blank");
        }
        if (partition < 0) {
            throw new IllegalArgumentException("partition must be >= 0");
        }
    }

    @Override
    public int compareTo(TopicPartition other) {
        int byTopic = topic.compareTo(other.topic);
        return byTopic != 0 ? byTopic : Integer.compare(partition, other.partition);
    }

    @Override
    public String toString() {
        return topic + "-" + partition;
    }
}
