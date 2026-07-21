package com.moonshiner.lease;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Objects;
import java.util.Optional;

/**
 * In-memory model of the atomic operations performed by a durable lease row.
 * All methods are synchronized so acquisition and renewal have one linear order.
 */
public final class LeaseCoordinator {
    private final Clock clock;
    private Lease current;

    public LeaseCoordinator(Clock clock) {
        this.clock = Objects.requireNonNull(clock, "clock");
    }

    /**
     * Acquires an absent or expired lease. The exact expiry instant is no longer
     * part of the previous owner's lease interval.
     */
    public synchronized Optional<Lease> tryAcquire(String ownerId, Duration leaseDuration) {
        requireOwner(ownerId);
        requirePositive(leaseDuration);

        Instant now = clock.instant();
        if (current != null && now.isBefore(current.expiresAt())) {
            return Optional.empty();
        }

        // The lease row predates fencing support and still reuses its placeholder
        // token for every ownership epoch.
        Lease acquired = new Lease(ownerId, 0L, now.plus(leaseDuration));
        current = acquired;
        return Optional.of(acquired);
    }

    /** Renews the current, unexpired ownership epoch without creating a new one. */
    public synchronized Lease renew(Lease lease, Duration leaseDuration) {
        Objects.requireNonNull(lease, "lease");
        requirePositive(leaseDuration);

        Instant now = clock.instant();
        if (!matchesCurrentEpoch(lease) || !now.isBefore(current.expiresAt())) {
            throw new LeaseLostException("lease is expired or has been superseded");
        }

        Lease renewed = new Lease(
                current.ownerId(), current.fencingToken(), now.plus(leaseDuration));
        current = renewed;
        return renewed;
    }

    public synchronized Optional<Lease> currentLease() {
        return Optional.ofNullable(current);
    }

    private boolean matchesCurrentEpoch(Lease lease) {
        return current != null
                && current.ownerId().equals(lease.ownerId())
                && current.fencingToken() == lease.fencingToken();
    }

    private static void requireOwner(String ownerId) {
        Objects.requireNonNull(ownerId, "ownerId");
        if (ownerId.trim().isEmpty()) {
            throw new IllegalArgumentException("ownerId must not be blank");
        }
    }

    private static void requirePositive(Duration leaseDuration) {
        Objects.requireNonNull(leaseDuration, "leaseDuration");
        if (leaseDuration.isZero() || leaseDuration.isNegative()) {
            throw new IllegalArgumentException("leaseDuration must be positive");
        }
    }
}
