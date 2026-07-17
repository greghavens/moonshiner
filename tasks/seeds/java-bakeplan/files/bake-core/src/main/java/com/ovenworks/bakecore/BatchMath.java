package com.ovenworks.bakecore;

/** Batch arithmetic shared by the planner and the costing sheet. */
public final class BatchMath {

    private BatchMath() {
    }

    /**
     * Grams of an ingredient needed for {@code batches} batches with a
     * waste allowance, rounded up to the whole gram: you can't buy a
     * fraction of a gram, and under-ordering stops the line.
     */
    public static int gramsWithWaste(int gramsPerBatch, int batches, int wastePct) {
        return Math.ceilDiv(gramsPerBatch * batches * (100 + wastePct), 100);
    }

    /**
     * How many batches cover an order, rounded up — a partial batch is
     * still a full batch on the floor. Zero units ordered means zero
     * batches.
     */
    public static int batchesForOrders(int unitsOrdered, int unitsPerBatch) {
        throw new UnsupportedOperationException("TODO: came over in the module split unfinished");
    }
}
