package com.brightforge.ledyield;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class PanelYieldStatsTest {

    @Test
    void rateIsPercentOfPixelsTested() {
        assertEquals(95.0, PanelYieldStats.rate(new PanelLot("RUN-A12", "atlas", 640, 608)), 1e-9);
        assertEquals(0.0, PanelYieldStats.rate(new PanelLot("RUN-Z00", "nova", 320, 0)), 1e-9);
        assertEquals(100.0, PanelYieldStats.rate(new PanelLot("RUN-Z01", "nova", 320, 320)), 1e-9);
    }

    @Test
    void lotValidationMessagesArePinned() {
        IllegalArgumentException tested = assertThrows(
                IllegalArgumentException.class, () -> new PanelLot("RUN-X", "vista", 0, 0));
        assertEquals("pixelsTested must be positive", tested.getMessage());

        IllegalArgumentException negative = assertThrows(
                IllegalArgumentException.class, () -> new PanelLot("RUN-X", "vista", 10, -1));
        assertEquals("pixelsLit must not be negative", negative.getMessage());

        IllegalArgumentException excess = assertThrows(
                IllegalArgumentException.class, () -> new PanelLot("RUN-X", "vista", 10, 11));
        assertEquals("pixelsLit exceeds pixelsTested", excess.getMessage());
    }

    @ParameterizedTest
    @CsvSource({
        "400, 360, SHIP",
        "400, 359, REWORK",
        "400, 280, REWORK",
        "400, 279, SCRAP",
        "320, 320, SHIP",
        "320, 0, SCRAP",
    })
    void verdictThresholdsSitAtNinetyAndSeventy(int tested, int lit, PanelYieldStats.Verdict want) {
        assertEquals(want, PanelYieldStats.classify(new PanelLot("RUN-T", "atlas", tested, lit)));
    }

    @Test
    void qaBoardIsSortedByLotCode() {
        List<PanelLot> lots = List.of(
                new PanelLot("RUN-C03", "vista", 800, 552),
                new PanelLot("RUN-A12", "atlas", 640, 608),
                new PanelLot("RUN-B07", "nova", 500, 379));
        assertEquals(
                List.of(
                        "RUN-A12 atlas 95.0% SHIP",
                        "RUN-B07 nova 75.8% REWORK",
                        "RUN-C03 vista 69.0% SCRAP"),
                PanelYieldStats.summarize(lots));
    }

    @Test
    void summaryOfNothingIsEmpty() {
        assertTrue(PanelYieldStats.summarize(List.of()).isEmpty());
    }
}
