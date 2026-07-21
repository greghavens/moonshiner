package com.moonshiner.reports;

import java.math.BigDecimal;
import java.time.Instant;

final class ReportTestData {
    static final BigDecimal AMOUNT = new BigDecimal("1234.50");
    static final Instant GENERATED_AT = Instant.parse("2024-01-15T12:00:00Z");
    static final String US_UTC_REPORT = "$1,234.50 | Jan 15, 2024 12:00 UTC";

    private ReportTestData() {
    }
}
