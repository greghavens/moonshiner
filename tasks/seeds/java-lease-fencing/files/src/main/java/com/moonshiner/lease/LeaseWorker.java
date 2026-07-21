package com.moonshiner.lease;

import java.time.Duration;
import java.util.Objects;
import java.util.Optional;

/** A worker that carries its lease epoch's fencing token to the result store. */
public final class LeaseWorker {
    private final String ownerId;
    private final Duration leaseDuration;
    private final LeaseCoordinator coordinator;
    private final FencedResultStore resultStore;
    private Lease lease;

    public LeaseWorker(
            String ownerId,
            Duration leaseDuration,
            LeaseCoordinator coordinator,
            FencedResultStore resultStore) {
        this.ownerId = Objects.requireNonNull(ownerId, "ownerId");
        this.leaseDuration = Objects.requireNonNull(leaseDuration, "leaseDuration");
        this.coordinator = Objects.requireNonNull(coordinator, "coordinator");
        this.resultStore = Objects.requireNonNull(resultStore, "resultStore");
    }

    public boolean tryAcquire() {
        Optional<Lease> acquired = coordinator.tryAcquire(ownerId, leaseDuration);
        if (!acquired.isPresent()) {
            return false;
        }
        lease = acquired.get();
        return true;
    }

    public void renew() {
        lease = coordinator.renew(requireLease(), leaseDuration);
    }

    public void commit(String result) {
        resultStore.commit(requireLease().fencingToken(), result);
    }

    public Optional<Lease> lease() {
        return Optional.ofNullable(lease);
    }

    private Lease requireLease() {
        if (lease == null) {
            throw new IllegalStateException("worker has not acquired a lease");
        }
        return lease;
    }
}
