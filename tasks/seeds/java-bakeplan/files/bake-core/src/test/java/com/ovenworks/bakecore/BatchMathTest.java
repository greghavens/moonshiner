package com.ovenworks.bakecore;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class BatchMathTest {

    @ParameterizedTest
    @CsvSource({
        "6000, 3, 5, 18900",
        "30, 3, 5, 95",
        "1000, 2, 0, 2000",
        "333, 1, 10, 367",
    })
    void wasteAllowanceRoundsUpToTheWholeGram(int gramsPerBatch, int batches, int wastePct, int want) {
        assertEquals(want, BatchMath.gramsWithWaste(gramsPerBatch, batches, wastePct));
    }

    @ParameterizedTest
    @CsvSource({
        "60, 20, 3",
        "25, 12, 3",
        "24, 12, 2",
        "1, 12, 1",
        "0, 12, 0",
    })
    void partialBatchesCountAsFullBatches(int unitsOrdered, int unitsPerBatch, int want) {
        assertEquals(want, BatchMath.batchesForOrders(unitsOrdered, unitsPerBatch));
    }

    @Test
    void batchSizeMustBePositive() {
        IllegalArgumentException e = assertThrows(
                IllegalArgumentException.class, () -> BatchMath.batchesForOrders(5, 0));
        assertEquals("unitsPerBatch must be positive", e.getMessage());
    }

    @Test
    void orderedUnitsMustNotBeNegative() {
        IllegalArgumentException e = assertThrows(
                IllegalArgumentException.class, () -> BatchMath.batchesForOrders(-1, 12));
        assertEquals("unitsOrdered must not be negative", e.getMessage());
    }
}
