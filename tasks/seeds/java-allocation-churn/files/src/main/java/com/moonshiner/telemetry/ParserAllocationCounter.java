package com.moonshiner.telemetry;

/**
 * Deterministic instrumentation for allocations owned by the parser.
 *
 * <p>The counter intentionally covers only throwaway parsing workspaces. It
 * does not count {@link TelemetryRecord} instances or their returned strings,
 * because those objects escape to the caller and are part of the result.</p>
 */
public final class ParserAllocationCounter {
    private long scratchInstances;
    private long scratchArrays;
    private long scratchCharacterCapacity;

    void scratchAllocated(int capacity) {
        scratchInstances++;
        scratchArrays++;
        scratchCharacterCapacity += capacity;
    }

    public long scratchInstances() {
        return scratchInstances;
    }

    public long scratchArrays() {
        return scratchArrays;
    }

    public long scratchCharacterCapacity() {
        return scratchCharacterCapacity;
    }
}
