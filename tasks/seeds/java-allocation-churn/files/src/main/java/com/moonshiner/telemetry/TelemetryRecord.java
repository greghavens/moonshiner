package com.moonshiner.telemetry;

import java.util.Objects;

/** One decoded telemetry input record. */
public final class TelemetryRecord {
    private final long sequence;
    private final String key;
    private final String payload;

    public TelemetryRecord(long sequence, String key, String payload) {
        this.sequence = sequence;
        this.key = Objects.requireNonNull(key, "key");
        this.payload = Objects.requireNonNull(payload, "payload");
    }

    public long sequence() {
        return sequence;
    }

    public String key() {
        return key;
    }

    public String payload() {
        return payload;
    }

    @Override
    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof TelemetryRecord)) {
            return false;
        }
        TelemetryRecord that = (TelemetryRecord) other;
        return sequence == that.sequence
                && key.equals(that.key)
                && payload.equals(that.payload);
    }

    @Override
    public int hashCode() {
        return Objects.hash(sequence, key, payload);
    }

    @Override
    public String toString() {
        return "TelemetryRecord{" + sequence + ", " + key + ", " + payload + "}";
    }
}
