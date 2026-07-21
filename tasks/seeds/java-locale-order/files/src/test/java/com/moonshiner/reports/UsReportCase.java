package com.moonshiner.reports;

import java.util.Locale;
import java.util.TimeZone;

public final class UsReportCase implements ReportCase {
    @Override
    public String name() {
        return "US report uses en-US and UTC";
    }

    @Override
    public void run() {
        String inheritedDefaults = Locale.getDefault().toLanguageTag()
                + "|"
                + TimeZone.getDefault().getID();
        TestAssertions.equal(
                "en-US|UTC",
                inheritedDefaults,
                "US report case inherited locale/time-zone state from a previous case");
        TestAssertions.equal(
                ReportTestData.US_UTC_REPORT,
                ReportFormatter.format(
                        ReportTestData.AMOUNT,
                        ReportTestData.GENERATED_AT),
                "US report rendering changed");
    }

    public static void main(String[] args) {
        new UsReportCase().run();
        System.out.println("PASS: US report case in isolation");
    }
}
