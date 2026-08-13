import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Asserts the exact wire shape of the requests the client produced, against the log kept by the
 * contract-pinned loopback mock. Nothing here contacts a live VMware endpoint.
 */
public final class WireVerifier {

    /**
     * One expected request.
     *
     * @param path the full raw request path, service base path included
     * @param body the exact entity expected, or null when the request must carry no entity at all
     * @param correlationExpected whether the specification declares {@code X-Correlation-Id} on
     *     this operation and the run configured a correlation id
     */
    public record Expect(
            String operationId,
            String method,
            String path,
            String body,
            boolean correlationExpected) {}

    private WireVerifier() {}

    /** Fails the run when a checked condition does not hold. */
    public static final class WireAssertionError extends AssertionError {
        public WireAssertionError(String message) {
            super(message);
        }
    }

    public static void check(String what, boolean condition) {
        if (!condition) {
            throw new WireAssertionError(what);
        }
    }

    public static void equal(String what, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new WireAssertionError(what + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    /**
     * Verifies the whole recorded exchange for one scenario.
     *
     * @param correlationId the configured correlation id, or null/empty when the run configured none
     */
    public static void verify(
            String scenario,
            MockSddcLcm mock,
            String token,
            String correlationId,
            List<Expect> expected) {

        List<MockSddcLcm.Recorded> actual = mock.log();
        String prefix = "[" + scenario + "] ";

        if (mock.offContractRequests() != 0) {
            throw new WireAssertionError(
                    prefix
                            + "client sent "
                            + mock.offContractRequests()
                            + " request(s) that match no operation the contract projects: "
                            + offContractLines(actual));
        }

        if (actual.size() != expected.size()) {
            throw new WireAssertionError(
                    prefix
                            + "expected "
                            + expected.size()
                            + " request(s) but observed "
                            + actual.size()
                            + "\n  expected:\n"
                            + render(expected)
                            + "\n  observed:\n"
                            + renderActual(actual));
        }

        boolean correlationConfigured = correlationId != null && !correlationId.isEmpty();

        for (int i = 0; i < expected.size(); i++) {
            Expect want = expected.get(i);
            MockSddcLcm.Recorded got = actual.get(i);
            String at = prefix + "request #" + (i + 1) + " (" + want.operationId() + ") ";

            equal(at + "operationId", want.operationId(), got.operationId());
            equal(at + "method", want.method(), got.method());
            equal(at + "path", want.path(), got.path());

            if (got.query() != null) {
                throw new WireAssertionError(
                        at + "carried a query string <" + got.query() + "> but the contract declares no query parameter");
            }

            equal(at + "Authorization", "Bearer " + token, got.header("Authorization"));
            equal(at + "Accept", "application/json", got.header("Accept"));

            if (want.body() == null) {
                equal(at + "body", "", got.body());
                if (got.hasHeader("Content-Type")) {
                    throw new WireAssertionError(
                            at
                                    + "sent Content-Type <"
                                    + got.header("Content-Type")
                                    + "> on a request that carries no entity");
                }
            } else {
                equal(at + "Content-Type", "application/json", got.header("Content-Type"));
                equal(at + "entity", want.body(), got.body());
                assertNoEmptyOptionals(at, want.body(), got.body());
            }

            boolean wantCorrelation = want.correlationExpected() && correlationConfigured;
            if (wantCorrelation) {
                equal(at + "X-Correlation-Id", correlationId, got.header("X-Correlation-Id"));
            } else if (got.hasHeader("X-Correlation-Id")) {
                throw new WireAssertionError(
                        at
                                + "sent X-Correlation-Id <"
                                + got.header("X-Correlation-Id")
                                + "> but "
                                + (correlationConfigured
                                        ? "the specification does not declare that header on this operation"
                                        : "no correlation id was configured"));
            }
        }
    }

    /**
     * Re-checks the entity member by member, so an implementation that serialises an unset optional
     * as null, an empty string, an empty object or a defaulted zero reports the offending member by
     * name rather than as an opaque string mismatch.
     */
    private static void assertNoEmptyOptionals(String at, String expectedBody, String actualBody) {
        Map<String, Object> want = MockSddcLcm.Json.obj(MockSddcLcm.Json.parse(expectedBody));
        Map<String, Object> got = MockSddcLcm.Json.obj(MockSddcLcm.Json.parse(actualBody));

        for (Map.Entry<String, Object> entry : got.entrySet()) {
            String member = entry.getKey();
            Object value = entry.getValue();
            if (!want.containsKey(member)) {
                throw new WireAssertionError(
                        at
                                + "serialised unset optional member '"
                                + member
                                + "' as <"
                                + describe(value)
                                + "> instead of omitting it");
            }
            equal(at + "member '" + member + "'", want.get(member), value);
        }
        Set<String> missing = new java.util.LinkedHashSet<>(want.keySet());
        missing.removeAll(got.keySet());
        if (!missing.isEmpty()) {
            throw new WireAssertionError(at + "omitted populated member(s) " + missing);
        }
    }

    private static String describe(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof String s) {
            return s.isEmpty() ? "\"\" (empty string)" : "\"" + s + "\"";
        }
        return String.valueOf(value);
    }

    /** Asserts a task was polled exactly this many times, so terminal detection is not guessed. */
    public static void polls(String scenario, MockSddcLcm mock, String taskId, int expected) {
        int actual = mock.taskPolls(taskId);
        if (actual != expected) {
            throw new WireAssertionError(
                    "["
                            + scenario
                            + "] getTask on "
                            + taskId
                            + " was called "
                            + actual
                            + " time(s), expected exactly "
                            + expected
                            + (actual > expected
                                    ? " (the client kept polling past the terminal status)"
                                    : " (the client stopped before the task reached a terminal status)"));
        }
    }

    /** Asserts no request at all reached the mock, for calls that must fail before any I/O. */
    public static void silent(String scenario, MockSddcLcm mock) {
        List<MockSddcLcm.Recorded> actual = mock.log();
        if (!actual.isEmpty()) {
            throw new WireAssertionError(
                    "[" + scenario + "] expected no request but observed:\n" + renderActual(actual));
        }
    }

    /** Asserts a diagnostic never repeats the bearer token. */
    public static void withoutToken(String scenario, String token, String text) {
        if (text != null && !token.isEmpty() && text.contains(token)) {
            throw new WireAssertionError("[" + scenario + "] a diagnostic leaked the bearer token");
        }
    }

    private static String render(List<Expect> expected) {
        List<String> lines = new ArrayList<>();
        for (Expect e : expected) {
            lines.add("    " + e.method() + " " + e.path() + (e.body() == null ? "" : " " + e.body()));
        }
        return String.join("\n", lines);
    }

    private static String renderActual(List<MockSddcLcm.Recorded> actual) {
        List<String> lines = new ArrayList<>();
        for (MockSddcLcm.Recorded r : actual) {
            lines.add(
                    "    "
                            + r.method()
                            + " "
                            + r.path()
                            + (r.query() == null ? "" : "?" + r.query())
                            + (r.body().isEmpty() ? "" : " " + r.body())
                            + (r.operationId() == null ? "   <-- off contract" : ""));
        }
        return String.join("\n", lines);
    }

    private static String offContractLines(List<MockSddcLcm.Recorded> actual) {
        List<String> lines = new ArrayList<>();
        for (MockSddcLcm.Recorded r : actual) {
            if (r.operationId() == null) {
                lines.add(r.method() + " " + r.path());
            }
        }
        return String.join(", ", lines);
    }
}
