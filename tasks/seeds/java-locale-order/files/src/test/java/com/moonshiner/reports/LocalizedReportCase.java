package com.moonshiner.reports;

import com.moonshiner.testing.ScopedDefaults;
import java.util.Locale;
import java.util.TimeZone;

final class LocalizedReportCase implements ReportCase {
    @Override
    public String name() {
        return "localized report uses fr-FR and Europe/Paris";
    }

    @Override
    public void run() throws Exception {
        ScopedDefaults.runWith(
                Locale.FRANCE,
                TimeZone.getTimeZone("Europe/Paris"),
                () -> {
                    TestAssertions.equal(
                            "fr-FR|Europe/Paris",
                            currentDefaults(),
                            "localized case did not receive its requested defaults");
                    TestAssertions.notEqual(
                            ReportTestData.US_UTC_REPORT,
                            ReportFormatter.format(
                                    ReportTestData.AMOUNT,
                                    ReportTestData.GENERATED_AT),
                            "localized report unexpectedly used the US/UTC rendering");
                });
    }

    private static String currentDefaults() {
        return Locale.getDefault().toLanguageTag()
                + "|"
                + TimeZone.getDefault().getID();
    }
}
