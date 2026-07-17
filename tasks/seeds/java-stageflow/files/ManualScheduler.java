import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

/**
 * Deterministic virtual-time TaskScheduler for the test suite. Time moves
 * only when advance() is called; due actions run on the caller's thread in
 * (due time, schedule order). Single-threaded by design — no real timers,
 * no sleeping, ever.
 *
 * TEST FIXTURE — part of the acceptance contract, do not modify.
 */
public final class ManualScheduler implements TaskScheduler {

    private static final class Job {
        final long dueNanos;
        final long seq;
        final Runnable action;
        boolean cancelled;
        boolean fired;

        Job(long dueNanos, long seq, Runnable action) {
            this.dueNanos = dueNanos;
            this.seq = seq;
            this.action = action;
        }
    }

    private final List<Job> jobs = new ArrayList<>();
    private long nowNanos;
    private long nextSeq;
    private int cancelled;
    private int fired;

    @Override
    public Handle schedule(Runnable action, Duration delay) {
        if (action == null) {
            throw new IllegalArgumentException("action must not be null");
        }
        if (delay == null || delay.isNegative()) {
            throw new IllegalArgumentException("delay must not be negative");
        }
        Job job = new Job(nowNanos + delay.toNanos(), nextSeq++, action);
        jobs.add(job);
        return () -> {
            if (job.fired || job.cancelled) {
                return false;
            }
            job.cancelled = true;
            cancelled++;
            return true;
        };
    }

    /** Moves virtual time forward and fires every due, uncancelled action. */
    public void advance(Duration delta) {
        if (delta == null || delta.isNegative()) {
            throw new IllegalArgumentException("delta must not be negative");
        }
        nowNanos += delta.toNanos();
        while (true) {
            Job next = null;
            for (Job job : jobs) {
                if (job.fired || job.cancelled || job.dueNanos > nowNanos) {
                    continue;
                }
                if (next == null || job.dueNanos < next.dueNanos
                        || (job.dueNanos == next.dueNanos && job.seq < next.seq)) {
                    next = job;
                }
            }
            if (next == null) {
                return;
            }
            next.fired = true;
            fired++;
            next.action.run();
        }
    }

    /** Scheduled, not yet fired, not cancelled. */
    public int pendingCount() {
        int n = 0;
        for (Job job : jobs) {
            if (!job.fired && !job.cancelled) {
                n++;
            }
        }
        return n;
    }

    public int scheduledCount() {
        return (int) nextSeq;
    }

    public int cancelledCount() {
        return cancelled;
    }

    public int firedCount() {
        return fired;
    }

    public Duration now() {
        return Duration.ofNanos(nowNanos);
    }
}
