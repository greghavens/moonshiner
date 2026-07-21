package com.moonshiner.reports;

import java.math.BigDecimal;
import java.text.NumberFormat;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.Objects;
import java.util.TimeZone;

/** Formats a report using the caller's process defaults. */
public final class ReportFormatter {
    private ReportFormatter() {
    }

    public static String format(BigDecimal amount, Instant generatedAt) {
        Objects.requireNonNull(amount, "amount");
        Objects.requireNonNull(generatedAt, "generatedAt");

        NumberFormat currency = NumberFormat.getCurrencyInstance();
        DateTimeFormatter timestamp = DateTimeFormatter
                .ofPattern("MMM d, uuuu HH:mm z", Locale.getDefault())
                .withZone(TimeZone.getDefault().toZoneId());
        return currency.format(amount) + " | " + timestamp.format(generatedAt);
    }
}
