package com.moonshiner.reports;

import java.util.Objects;

final class TestAssertions {
    private TestAssertions() {
    }

    static void equal(Object expected, Object actual, String message) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(
                    message + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    static void notEqual(Object unexpected, Object actual, String message) {
        if (Objects.equals(unexpected, actual)) {
            throw new AssertionError(
                    message + ": did not expect <" + unexpected + ">");
        }
    }
}
