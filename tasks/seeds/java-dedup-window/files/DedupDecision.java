/** Result of observing one partition sequence number. */
public enum DedupDecision {
    ACCEPTED,
    DUPLICATE,
    TOO_OLD
}
