package com.brightforge.ledyield;

/** One production lot of LED panels put through the end-of-line lit-pixel test. */
public record PanelLot(String lotCode, String model, int pixelsTested, int pixelsLit) {

    public PanelLot {
        if (pixelsTested <= 0) {
            throw new IllegalArgumentException("pixelsTested must be positive");
        }
        if (pixelsLit < 0) {
            throw new IllegalArgumentException("pixelsLit must not be negative");
        }
        if (pixelsLit > pixelsTested) {
            throw new IllegalArgumentException("pixelsLit exceeds pixelsTested");
        }
    }
}
