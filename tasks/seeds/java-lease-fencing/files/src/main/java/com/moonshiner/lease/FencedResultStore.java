package com.moonshiner.lease;

import java.util.Optional;

/** A downstream resource that remembers the greatest fencing token it has seen. */
public final class FencedResultStore {
    private long highestAcceptedToken = Long.MIN_VALUE;
    private String result;

    /**
     * Repeated writes from one lease epoch are valid. A write from an older
     * epoch is rejected once a newer token has reached this resource.
     */
    public synchronized void commit(long fencingToken, String newResult) {
        if (fencingToken < highestAcceptedToken) {
            throw new StaleFencingTokenException(fencingToken, highestAcceptedToken);
        }
        highestAcceptedToken = fencingToken;
        result = newResult;
    }

    public synchronized Optional<String> result() {
        return Optional.ofNullable(result);
    }

    public synchronized long highestAcceptedToken() {
        return highestAcceptedToken;
    }
}
