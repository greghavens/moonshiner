package store;

import java.util.HashMap;
import java.util.Map;
import java.util.TreeSet;
import java.util.function.Supplier;

/**
 * Occupied-slot snapshots per berth, kept warm for the availability board.
 * Reads rebuild a missing entry lazily; write paths invalidate the berth
 * they touched so the next read recomputes from the booking ledger.
 */
public final class AvailabilityCache {
    private final Map<String, TreeSet<Integer>> occupiedByBerth = new HashMap<>();

    /** Return the cached snapshot for the berth, computing it on first use. */
    public TreeSet<Integer> occupied(String berthCode, Supplier<TreeSet<Integer>> compute) {
        return occupiedByBerth.computeIfAbsent(berthCode, code -> compute.get());
    }

    public void invalidate(String berthCode) {
        occupiedByBerth.remove(berthCode);
    }
}
