package com.moonshiner.lease;

import java.time.Instant;
import java.util.Objects;

/** An immutable grant for one lease ownership epoch. */
public final class Lease {
    private final String ownerId;
    private final long fencingToken;
    private final Instant expiresAt;

    public Lease(String ownerId, long fencingToken, Instant expiresAt) {
        this.ownerId = Objects.requireNonNull(ownerId, "ownerId");
        this.fencingToken = fencingToken;
        this.expiresAt = Objects.requireNonNull(expiresAt, "expiresAt");
    }

    public String ownerId() {
        return ownerId;
    }

    public long fencingToken() {
        return fencingToken;
    }

    public Instant expiresAt() {
        return expiresAt;
    }

    @Override
    public String toString() {
        return "Lease{ownerId='" + ownerId + "', fencingToken=" + fencingToken
                + ", expiresAt=" + expiresAt + "}";
    }
}
