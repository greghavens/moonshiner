package com.brightforge.ledyield;

import java.util.List;

/** Lit-pixel yield reporting: rate, verdict, and the QA board. */
public final class PanelYieldStats {

    /** Lot verdict per the factory QA manual. */
    public enum Verdict {
        SHIP,
        REWORK,
        SCRAP
    }

    private PanelYieldStats() {
    }

    /** Lit-pixel yield as a percentage of pixels tested. */
    public static double rate(PanelLot lot) {
        return lot.pixelsLit() * 100.0 / lot.pixelsTested();
    }

    /** SHIP at 90% or better, REWORK at 70% or better, SCRAP below. */
    public static Verdict classify(PanelLot lot) {
        throw new UnsupportedOperationException("TODO: verdict thresholds");
    }

    /** One line per lot, sorted by lot code. */
    public static List<String> summarize(List<PanelLot> lots) {
        throw new UnsupportedOperationException("TODO: QA board");
    }
}
