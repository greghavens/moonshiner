package com.moonshiner.ledger;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.util.Objects;
import java.util.regex.Pattern;

/**
 * Converts the persisted representation used by the ledger's version 1 schema
 * (a signed count of cents) to the fixed-scale decimal representation used by
 * version 2.
 */
public final class LedgerAmountMigration {
    public static final int MINOR_UNITS_VERSION = 1;
    public static final int DECIMAL_VERSION = 2;

    private static final Pattern CANONICAL_MINOR_UNITS =
            Pattern.compile("-?(0|[1-9][0-9]*)");
    private static final Pattern CANONICAL_DECIMAL =
            Pattern.compile("-?(0|[1-9][0-9]*)\\.[0-9]{2}");

    private LedgerAmountMigration() {
    }

    /**
     * Migrates one stored amount. The method is also the entry point used when
     * a batch can contain rows written by both schema versions.
     */
    public static PersistedAmount migrate(PersistedAmount stored) {
        Objects.requireNonNull(stored, "stored");

        if (stored.getSchemaVersion() == MINOR_UNITS_VERSION) {
            long minorUnits = parseMinorUnits(stored.getAmount());
            return new PersistedAmount(DECIMAL_VERSION, fromMinorUnits(minorUnits));
        }
        if (stored.getSchemaVersion() == DECIMAL_VERSION) {
            validateDecimal(stored.getAmount());

            // Normalize persisted numeric text before writing the version 2 row.
            String normalized = new BigDecimal(stored.getAmount())
                    .movePointLeft(2)
                    .setScale(2, RoundingMode.UNNECESSARY)
                    .toPlainString();
            return new PersistedAmount(DECIMAL_VERSION, normalized);
        }

        throw new IllegalArgumentException(
                "Unsupported ledger amount schema version: " + stored.getSchemaVersion());
    }

    /** Returns the canonical version 2 representation for a minor-unit value. */
    public static String fromMinorUnits(long minorUnits) {
        return BigDecimal.valueOf(minorUnits, 2).toPlainString();
    }

    /**
     * Converts a canonical version 2 amount back to its exact signed 64-bit
     * minor-unit value.
     */
    public static long toMinorUnits(String decimalAmount) {
        validateDecimal(decimalAmount);
        try {
            return new BigDecimal(decimalAmount).movePointRight(2).longValueExact();
        } catch (ArithmeticException ex) {
            throw new IllegalArgumentException(
                    "Decimal amount is outside the signed 64-bit minor-unit range: "
                            + decimalAmount,
                    ex);
        }
    }

    private static long parseMinorUnits(String value) {
        if (value == null || !CANONICAL_MINOR_UNITS.matcher(value).matches()) {
            throw new IllegalArgumentException("Invalid canonical minor-unit amount: " + value);
        }
        try {
            return new BigInteger(value).longValueExact();
        } catch (ArithmeticException ex) {
            throw new IllegalArgumentException(
                    "Minor-unit amount is outside the signed 64-bit range: " + value,
                    ex);
        }
    }

    private static void validateDecimal(String value) {
        if (value == null || !CANONICAL_DECIMAL.matcher(value).matches()) {
            throw new IllegalArgumentException("Invalid canonical decimal amount: " + value);
        }

        // Parsing and converting here also gives callers one consistent overflow
        // rule for persisted v2 values and values passed to toMinorUnits.
        try {
            new BigDecimal(value).movePointRight(2).longValueExact();
        } catch (ArithmeticException ex) {
            throw new IllegalArgumentException(
                    "Decimal amount is outside the signed 64-bit minor-unit range: " + value,
                    ex);
        }
    }

    public static final class PersistedAmount {
        private final int schemaVersion;
        private final String amount;

        public PersistedAmount(int schemaVersion, String amount) {
            this.schemaVersion = schemaVersion;
            this.amount = amount;
        }

        public int getSchemaVersion() {
            return schemaVersion;
        }

        public String getAmount() {
            return amount;
        }

        @Override
        public boolean equals(Object other) {
            if (this == other) {
                return true;
            }
            if (!(other instanceof PersistedAmount)) {
                return false;
            }
            PersistedAmount that = (PersistedAmount) other;
            return schemaVersion == that.schemaVersion && Objects.equals(amount, that.amount);
        }

        @Override
        public int hashCode() {
            return Objects.hash(schemaVersion, amount);
        }

        @Override
        public String toString() {
            return "PersistedAmount{schemaVersion=" + schemaVersion + ", amount='" + amount + "'}";
        }
    }
}
