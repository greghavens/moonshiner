package moonshiner.routes;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import java.util.regex.Pattern;

/** Parses dot-separated lowercase route segments. */
public final class RouteSpecParser {
    /*
     * The inner and outer repetitions overlap. On a long segment followed by
     * an invalid character, the matcher explores exponentially many ways to
     * divide the segment before it can reject the input.
     */
    private static final Pattern ROUTE_PATTERN =
            Pattern.compile("(?:[a-z]+)+(?:\\.(?:[a-z]+)+)*");

    private RouteSpecParser() {
    }

    public static List<String> parse(String input) {
        return parseMeasured(input, Long.MAX_VALUE).segments();
    }

    /**
     * Parses a route while counting character inspections performed by the
     * parser. The budget makes pathological work observable without timing.
     */
    public static ParseResult parseMeasured(String input, long operationLimit) {
        Objects.requireNonNull(input, "input");
        if (operationLimit < 1) {
            throw new IllegalArgumentException("operationLimit must be positive");
        }

        CountingCharSequence measured = new CountingCharSequence(input, operationLimit);
        if (!ROUTE_PATTERN.matcher(measured).matches()) {
            throw new SyntaxException(firstErrorOffset(measured), measured.operationCount());
        }

        List<String> segments = Collections.unmodifiableList(
                Arrays.asList(input.split("\\.", -1)));
        return new ParseResult(segments, measured.operationCount());
    }

    private static int firstErrorOffset(CountingCharSequence input) {
        if (input.isEmpty()) {
            return 0;
        }

        boolean expectingLetter = true;
        for (int index = 0; index < input.length(); index++) {
            char current = input.charAt(index);
            if (current >= 'a' && current <= 'z') {
                expectingLetter = false;
            } else if (current == '.' && !expectingLetter) {
                expectingLetter = true;
            } else {
                return index;
            }
        }
        return input.length();
    }

    public static final class ParseResult {
        private final List<String> segments;
        private final long operationCount;

        private ParseResult(List<String> segments, long operationCount) {
            this.segments = segments;
            this.operationCount = operationCount;
        }

        public List<String> segments() {
            return segments;
        }

        public long operationCount() {
            return operationCount;
        }
    }

    public static final class SyntaxException extends IllegalArgumentException {
        private final int errorOffset;
        private final long operationCount;

        private SyntaxException(int errorOffset, long operationCount) {
            super("Invalid route at index " + errorOffset);
            this.errorOffset = errorOffset;
            this.operationCount = operationCount;
        }

        public int errorOffset() {
            return errorOffset;
        }

        public long operationCount() {
            return operationCount;
        }
    }

    public static final class OperationLimitExceededException extends IllegalStateException {
        private final long operationLimit;

        private OperationLimitExceededException(long operationLimit) {
            super("Parser exceeded " + operationLimit + " character inspections");
            this.operationLimit = operationLimit;
        }

        public long operationLimit() {
            return operationLimit;
        }
    }

    private static final class CountingCharSequence implements CharSequence {
        private final String value;
        private final long operationLimit;
        private long operationCount;

        private CountingCharSequence(String value, long operationLimit) {
            this.value = value;
            this.operationLimit = operationLimit;
        }

        @Override
        public int length() {
            return value.length();
        }

        @Override
        public char charAt(int index) {
            operationCount++;
            if (operationCount > operationLimit) {
                throw new OperationLimitExceededException(operationLimit);
            }
            return value.charAt(index);
        }

        @Override
        public CharSequence subSequence(int start, int end) {
            return value.subSequence(start, end);
        }

        private long operationCount() {
            return operationCount;
        }
    }
}
