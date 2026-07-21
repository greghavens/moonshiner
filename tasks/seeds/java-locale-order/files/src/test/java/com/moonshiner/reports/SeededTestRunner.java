package com.moonshiner.reports;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.TimeZone;

public final class SeededTestRunner {
    private SeededTestRunner() {
    }

    public static void main(String[] args) {
        if (args.length != 1) {
            throw new IllegalArgumentException("expected exactly one shuffle seed");
        }

        long seed = Long.parseLong(args[0]);
        List<ReportCase> cases = new ArrayList<>(List.of(
                new UsReportCase(),
                new ExactRestorationCase(),
                new LocalizedReportCase()));
        Collections.shuffle(cases, new Random(seed));

        Locale suiteLocale = Locale.getDefault();
        TimeZone suiteTimeZone = TimeZone.getDefault();
        System.out.println("test-order seed: " + seed);
        System.out.println("case order: " + caseNames(cases));

        List<String> failures = new ArrayList<>();
        for (ReportCase testCase : cases) {
            try {
                testCase.run();
                System.out.println("PASS: " + testCase.name());
            } catch (Throwable failure) {
                failures.add(testCase.name() + ": " + failure);
                System.err.println("FAIL: " + testCase.name());
                failure.printStackTrace(System.err);
            }

            Locale afterLocale = Locale.getDefault();
            TimeZone afterTimeZone = TimeZone.getDefault();
            if (!suiteLocale.equals(afterLocale) || !suiteTimeZone.equals(afterTimeZone)) {
                String failure = testCase.name()
                        + " leaked process defaults: expected <"
                        + describe(suiteLocale, suiteTimeZone)
                        + "> but was <"
                        + describe(afterLocale, afterTimeZone)
                        + ">";
                failures.add(failure);
                System.err.println("FAIL: " + failure);
            }
        }

        if (!failures.isEmpty()) {
            throw new AssertionError(
                    failures.size() + " failure(s) under seed " + seed + ": " + failures);
        }
        System.out.println("PASS: shuffled suite restored locale and time zone after every case");
    }

    private static String describe(Locale locale, TimeZone timeZone) {
        return locale.toLanguageTag()
                + "|"
                + timeZone.getID();
    }

    private static String caseNames(List<ReportCase> cases) {
        List<String> names = new ArrayList<>();
        for (ReportCase testCase : cases) {
            names.add(testCase.name());
        }
        return String.join(", ", names);
    }
}
