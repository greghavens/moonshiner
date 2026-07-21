/** Partition checkpoint operations supplied by the group coordinator. */
public interface CheckpointStore {
    /** Returns the next offset a newly assigned member should process. */
    long committed(TopicPartition partition);

    /** Commits a next offset on behalf of the partition's current owner. */
    void commit(String memberId, TopicPartition partition, long nextOffset);
}
