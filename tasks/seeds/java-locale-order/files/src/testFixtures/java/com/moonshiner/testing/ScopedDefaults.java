package com.moonshiner.testing;

import java.util.Locale;
import java.util.Objects;
import java.util.TimeZone;

/** Test support for code that deliberately reads process-wide defaults. */
public final class ScopedDefaults {
    private ScopedDefaults() {
    }

    @FunctionalInterface
    public interface CheckedRunnable {
        void run() throws Exception;
    }

    public static void runWith(
            Locale locale,
            TimeZone timeZone,
            CheckedRunnable action) throws Exception {
        Objects.requireNonNull(locale, "locale");
        Objects.requireNonNull(timeZone, "timeZone");
        Objects.requireNonNull(action, "action");

        Locale.setDefault(locale);
        TimeZone.setDefault(timeZone);
        action.run();
    }
}
