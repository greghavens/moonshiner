package com.moonshiner.reports;

import com.moonshiner.testing.ScopedDefaults;
import java.util.Locale;
import java.util.SimpleTimeZone;
import java.util.TimeZone;

final class ExactRestorationCase implements ReportCase {
    @Override
    public String name() {
        return "scope restores the defaults that were active on entry";
    }

    @Override
    public void run() throws Exception {
        Locale inheritedLocale = Locale.getDefault();
        Locale inheritedDisplayLocale = Locale.getDefault(Locale.Category.DISPLAY);
        Locale inheritedFormatLocale = Locale.getDefault(Locale.Category.FORMAT);
        TimeZone inheritedTimeZone = TimeZone.getDefault();
        Locale entryLocale = Locale.JAPAN;
        Locale entryDisplayLocale = Locale.CANADA_FRENCH;
        Locale entryFormatLocale = Locale.UK;
        TimeZone entryTimeZone = new SimpleTimeZone(
                9 * 60 * 60 * 1000,
                "Moonshiner/Entry");

        try {
            Locale.setDefault(entryLocale);
            Locale.setDefault(Locale.Category.DISPLAY, entryDisplayLocale);
            Locale.setDefault(Locale.Category.FORMAT, entryFormatLocale);
            TimeZone.setDefault(entryTimeZone);

            int[] callbackCalls = {0};
            ScopedDefaults.runWith(
                    Locale.GERMANY,
                    TimeZone.getTimeZone("Europe/Berlin"),
                    () -> {
                        callbackCalls[0]++;
                        TestAssertions.equal(
                                "de-DE|de-DE|de-DE|Europe/Berlin",
                                currentDefaults(),
                                "scope did not install the requested defaults");
                    });

            TestAssertions.equal(
                    1,
                    callbackCalls[0],
                    "scope did not invoke the callback exactly once");

            TestAssertions.equal(
                    entryLocale,
                    Locale.getDefault(),
                    "scope did not restore the entry locale");
            TestAssertions.equal(
                    entryDisplayLocale,
                    Locale.getDefault(Locale.Category.DISPLAY),
                    "scope did not restore the entry display locale");
            TestAssertions.equal(
                    entryFormatLocale,
                    Locale.getDefault(Locale.Category.FORMAT),
                    "scope did not restore the entry format locale");
            TestAssertions.equal(
                    entryTimeZone,
                    TimeZone.getDefault(),
                    "scope did not restore the entry time zone");

            Exception callbackFailure = new Exception("expected callback failure");
            try {
                ScopedDefaults.runWith(
                        Locale.ITALY,
                        TimeZone.getTimeZone("Europe/Rome"),
                        () -> {
                            TestAssertions.equal(
                                    "it-IT|it-IT|it-IT|Europe/Rome",
                                    currentDefaults(),
                                    "throwing scope did not install the requested defaults");
                            throw callbackFailure;
                        });
                throw new AssertionError("scope swallowed the callback failure");
            } catch (Exception actualFailure) {
                TestAssertions.equal(
                        callbackFailure,
                        actualFailure,
                        "scope did not propagate the callback failure");
            }

            TestAssertions.equal(
                    entryLocale,
                    Locale.getDefault(),
                    "throwing scope did not restore the entry locale");
            TestAssertions.equal(
                    entryDisplayLocale,
                    Locale.getDefault(Locale.Category.DISPLAY),
                    "throwing scope did not restore the entry display locale");
            TestAssertions.equal(
                    entryFormatLocale,
                    Locale.getDefault(Locale.Category.FORMAT),
                    "throwing scope did not restore the entry format locale");
            TestAssertions.equal(
                    entryTimeZone,
                    TimeZone.getDefault(),
                    "throwing scope did not restore the entry time zone");
        } finally {
            Locale.setDefault(inheritedLocale);
            Locale.setDefault(Locale.Category.DISPLAY, inheritedDisplayLocale);
            Locale.setDefault(Locale.Category.FORMAT, inheritedFormatLocale);
            TimeZone.setDefault(inheritedTimeZone);
        }
    }

    private static String currentDefaults() {
        return Locale.getDefault().toLanguageTag()
                + "|"
                + Locale.getDefault(Locale.Category.DISPLAY).toLanguageTag()
                + "|"
                + Locale.getDefault(Locale.Category.FORMAT).toLanguageTag()
                + "|"
                + TimeZone.getDefault().getID();
    }
}
