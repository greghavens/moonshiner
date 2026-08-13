import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Asserts the exact wire shape of the requests the client produced, read back from the log kept by
 * the contract-pinned loopback fixture, and the provenance of the derived contract itself.
 *
 * <p>Nothing here opens a network connection: the provenance checks read the two files under
 * {@code docs/} and the wire checks read {@link MockVcfAutomation#log()}. No live VMware endpoint
 * is contacted by this verifier.
 */
public final class WireVerifier {

    /** The five operations docs/contract.json projects, and the reference page behind each. */
    private static final String REFERENCE =
            "https://developer.broadcom.com/xapis/vm-apps-org-provisioning-service/latest/";

    private static final Map<String, String[]> EXPECTED_OPERATIONS = new LinkedHashMap<>();

    static {
        EXPECTED_OPERATIONS.put(
                "retrieveAuthToken",
                new String[] {"POST", "/iaas/api/login", REFERENCE + "iaas/api/login/post/"});
        EXPECTED_OPERATIONS.put(
                "getAboutPage",
                new String[] {"GET", "/iaas/api/about", REFERENCE + "iaas/api/about/get/"});
        EXPECTED_OPERATIONS.put(
                "createMachine",
                new String[] {"POST", "/iaas/api/machines", REFERENCE + "iaas/api/machines/post/"});
        EXPECTED_OPERATIONS.put(
                "getRequestTracker",
                new String[] {
                    "GET",
                    "/iaas/api/request-tracker/{id}",
                    REFERENCE + "iaas/api/request-tracker/id/get/"
                });
        EXPECTED_OPERATIONS.put(
                "getMachine",
                new String[] {
                    "GET", "/iaas/api/machines/{id}", REFERENCE + "iaas/api/machines/id/get/"
                });
    }

    /**
     * One expected request.
     *
     * @param query the exact raw query string expected, or null when the request must carry none
     * @param body the exact entity expected, or null when the request must carry no entity at all
     * @param authenticated whether the contract requires the bearer token on this operation
     */
    public record Expect(
            String operationId,
            String method,
            String path,
            String query,
            String body,
            boolean authenticated) {

        public static Expect entity(String operationId, String path, String query, String body) {
            return new Expect(operationId, "POST", path, query, body, true);
        }

        public static Expect read(String operationId, String path, String query) {
            return new Expect(operationId, "GET", path, query, null, true);
        }
    }

    /** Fails the run when a checked condition does not hold. */
    public static final class WireAssertionError extends AssertionError {
        public WireAssertionError(String message) {
            super(message);
        }
    }

    private WireVerifier() {}

    public static void check(String what, boolean condition) {
        if (!condition) {
            throw new WireAssertionError(what);
        }
    }

    public static void equal(String what, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new WireAssertionError(
                    what + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    // ------------------------------------------------------------- provenance

    /**
     * Checks that the derived contract states plainly that it comes from reference documentation
     * rather than a published specification, and that every operation it names is backed by a
     * recorded Broadcom reference page with the date it was read.
     */
    public static void provenance(Path contractPath, Path sourcesPath) throws Exception {
        Map<String, Object> contract = readObject(contractPath);
        Map<String, Object> source = MockVcfAutomation.Json.obj(contract.get("source"));

        equal("contract source kind", "reference-documentation", source.get("kind"));
        for (String absent :
                List.of("specification_repository", "specification_commit_sha", "specification_path")) {
            check(
                    "contract must not claim a specification for VCF Automation ("
                            + absent
                            + " is set)",
                    source.containsKey(absent) && source.get(absent) == null);
        }
        String statement = MockVcfAutomation.Json.str(source, "statement");
        check("contract source carries no statement of what it was derived from", statement != null);
        check(
                "contract source statement does not say plainly that no specification backs it",
                statement.toLowerCase().contains("not")
                        && statement.toLowerCase().contains("specification"));
        check(
                "contract does not record the Broadcom developer portal as its publisher",
                String.valueOf(source.get("catalog_url")).startsWith("https://developer.broadcom.com"));
        check(
                "contract does not point at its provenance record",
                "docs/official_sources.json".equals(source.get("provenance_record")));

        Map<String, String[]> declared = new LinkedHashMap<>();
        for (Object entry : MockVcfAutomation.Json.arr(contract.get("operations"))) {
            Map<String, Object> operation = MockVcfAutomation.Json.obj(entry);
            declared.put(
                    MockVcfAutomation.Json.str(operation, "operationId"),
                    new String[] {
                        MockVcfAutomation.Json.str(operation, "method"),
                        MockVcfAutomation.Json.str(operation, "path"),
                        MockVcfAutomation.Json.str(operation, "documentation_url")
                    });
        }
        assertOperations("docs/contract.json", declared);

        Map<String, Object> sources = readObject(sourcesPath);
        check(
                "docs/official_sources.json does not record the date the reference was read",
                MockVcfAutomation.Json.str(sources, "fetched_date") != null);
        check(
                "docs/official_sources.json claims a specification repository for VCF Automation",
                sources.containsKey("specification_repository")
                        && sources.get("specification_repository") == null);
        List<Object> indexPages = MockVcfAutomation.Json.arr(sources.get("index_pages"));
        check(
                "docs/official_sources.json records no index or landing page",
                indexPages.size() >= 2);
        for (Object entry : indexPages) {
            Map<String, Object> page = MockVcfAutomation.Json.obj(entry);
            String url = MockVcfAutomation.Json.str(page, "url");
            check(
                    "index page url is not a Broadcom developer portal page: " + url,
                    url != null && url.startsWith("https://developer.broadcom.com"));
            check(
                    "index page " + url + " does not say what it documents",
                    MockVcfAutomation.Json.str(page, "documents") != null);
            check(
                    "index page " + url + " does not record the date it was read",
                    MockVcfAutomation.Json.str(page, "fetched_date") != null);
        }

        Map<String, String[]> recorded = new LinkedHashMap<>();
        for (Object entry : MockVcfAutomation.Json.arr(sources.get("operations"))) {
            Map<String, Object> operation = MockVcfAutomation.Json.obj(entry);
            String operationId = MockVcfAutomation.Json.str(operation, "operationId");
            check(
                    "source record for " + operationId + " does not name the page it documents",
                    MockVcfAutomation.Json.str(operation, "documents") != null);
            check(
                    "source record for " + operationId + " does not record the date it was read",
                    MockVcfAutomation.Json.str(operation, "fetched_date") != null);
            recorded.put(
                    operationId,
                    new String[] {
                        MockVcfAutomation.Json.str(operation, "method"),
                        MockVcfAutomation.Json.str(operation, "path"),
                        MockVcfAutomation.Json.str(operation, "url")
                    });
        }
        assertOperations("docs/official_sources.json", recorded);
    }

    private static void assertOperations(String where, Map<String, String[]> actual) {
        equal(where + " operation set", EXPECTED_OPERATIONS.keySet(), actual.keySet());
        for (Map.Entry<String, String[]> expected : EXPECTED_OPERATIONS.entrySet()) {
            String[] want = expected.getValue();
            String[] got = actual.get(expected.getKey());
            String at = where + " operation " + expected.getKey() + " ";
            equal(at + "method", want[0], got[0]);
            equal(at + "path", want[1], got[1]);
            equal(at + "reference page", want[2], got[2]);
        }
    }

    private static Map<String, Object> readObject(Path path) throws Exception {
        return MockVcfAutomation.Json.obj(
                MockVcfAutomation.Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    // ------------------------------------------------------------- wire shape

    /** Verifies the whole recorded exchange for one scenario. */
    public static void verify(
            String scenario, MockVcfAutomation mock, String bearerToken, List<Expect> expected) {

        List<MockVcfAutomation.Recorded> actual = mock.log();
        String prefix = "[" + scenario + "] ";

        if (mock.offContractRequests() != 0) {
            throw new WireAssertionError(
                    prefix
                            + "client sent "
                            + mock.offContractRequests()
                            + " request(s) that match no operation docs/contract.json names: "
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
                            + renderExpected(expected)
                            + "\n  observed:\n"
                            + renderActual(actual));
        }

        for (int i = 0; i < expected.size(); i++) {
            Expect want = expected.get(i);
            MockVcfAutomation.Recorded got = actual.get(i);
            String at = prefix + "request #" + (i + 1) + " (" + want.operationId() + ") ";

            equal(at + "operationId", want.operationId(), got.operationId());
            equal(at + "method", want.method(), got.method());
            equal(at + "path", want.path(), got.path());
            assertQuery(at, want.query(), got.query());

            if (want.authenticated()) {
                equal(at + "Authorization", "Bearer " + bearerToken, got.header("Authorization"));
            } else if (got.hasHeader("Authorization")) {
                throw new WireAssertionError(
                        at
                                + "sent an Authorization header on the operation that obtains the"
                                + " token, before any token exists");
            }
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
                assertMembers(at, want.body(), got.body());
                equal(at + "entity", want.body(), got.body());
            }
        }
    }

    private static void assertQuery(String at, String expected, String actual) {
        List<String> want = splitQuery(expected);
        List<String> got = splitQuery(actual);

        for (String parameter : got) {
            String name = parameter.substring(0, Math.max(parameter.indexOf('='), 0));
            String value = parameter.contains("=")
                    ? parameter.substring(parameter.indexOf('=') + 1)
                    : null;
            if (value != null && value.isEmpty()) {
                throw new WireAssertionError(
                        at + "sent the optional query parameter '" + name + "' with an empty value");
            }
            if (want.stream().noneMatch(candidate -> candidate.startsWith(name + "="))) {
                throw new WireAssertionError(
                        at
                                + "sent query parameter '"
                                + name
                                + "' the contract does not call for here (whole query: <"
                                + actual
                                + ">)");
            }
        }
        for (String parameter : want) {
            String name = parameter.substring(0, parameter.indexOf('='));
            if (got.stream().noneMatch(candidate -> candidate.startsWith(name + "="))) {
                throw new WireAssertionError(
                        at + "omitted the query parameter '" + parameter + "'");
            }
        }
        equal(at + "query string", expected, actual);
    }

    private static List<String> splitQuery(String query) {
        if (query == null || query.isEmpty()) {
            return List.of();
        }
        return List.of(query.split("&", -1));
    }

    /**
     * Re-checks the entity member by member, so an implementation that serialises an unset optional
     * as null, as an empty string, as an empty array or as an empty object reports the offending
     * member by name rather than as an opaque string mismatch.
     */
    private static void assertMembers(String at, String expectedBody, String actualBody) {
        Map<String, Object> want;
        Map<String, Object> got;
        try {
            want = MockVcfAutomation.Json.obj(MockVcfAutomation.Json.parse(expectedBody));
            got = MockVcfAutomation.Json.obj(MockVcfAutomation.Json.parse(actualBody));
        } catch (RuntimeException malformed) {
            throw new WireAssertionError(
                    at + "entity is not a JSON object: <" + actualBody + ">");
        }
        for (Map.Entry<String, Object> entry : got.entrySet()) {
            String member = entry.getKey();
            Object value = entry.getValue();
            if (!want.containsKey(member)) {
                throw new WireAssertionError(
                        at
                                + "serialised the unset optional member '"
                                + member
                                + "' as <"
                                + describe(value)
                                + "> instead of omitting it");
            }
            equal(at + "member '" + member + "'", want.get(member), value);
        }
        Set<String> missing = new LinkedHashSet<>(want.keySet());
        missing.removeAll(got.keySet());
        if (!missing.isEmpty()) {
            throw new WireAssertionError(at + "omitted populated member(s) " + missing);
        }
    }

    private static String describe(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof String text) {
            return text.isEmpty() ? "\"\" (empty string)" : "\"" + text + "\"";
        }
        if (value instanceof List<?> list && list.isEmpty()) {
            return "[] (empty array)";
        }
        if (value instanceof Map<?, ?> map && map.isEmpty()) {
            return "{} (empty object)";
        }
        return MockVcfAutomation.Json.stringify(value);
    }

    /**
     * Asserts a request tracker was read exactly this many times, so reaching a terminal state is
     * observed rather than assumed.
     */
    public static void polls(String scenario, MockVcfAutomation mock, String requestId, int expected) {
        int actual = mock.trackerPolls(requestId);
        if (actual != expected) {
            throw new WireAssertionError(
                    "["
                            + scenario
                            + "] getRequestTracker was called "
                            + actual
                            + " time(s) for request "
                            + requestId
                            + ", expected exactly "
                            + expected
                            + (actual > expected
                                    ? " (the client kept polling past the terminal status)"
                                    : " (the client stopped before the request reached a terminal"
                                            + " status)"));
        }
    }

    /** Asserts a tracker was polled at least this many times, for the deadline scenarios. */
    public static void pollsAtLeast(
            String scenario, MockVcfAutomation mock, String requestId, int minimum) {
        int actual = mock.trackerPolls(requestId);
        if (actual < minimum) {
            throw new WireAssertionError(
                    "["
                            + scenario
                            + "] getRequestTracker was called "
                            + actual
                            + " time(s) for request "
                            + requestId
                            + ", expected at least "
                            + minimum);
        }
    }

    /** Asserts successive tracker reads were separated by at least the requested polling delay. */
    public static void pollsRespectDelay(
            String scenario,
            MockVcfAutomation mock,
            String requestId,
            java.time.Duration minimumDelay) {
        List<Long> times = mock.trackerPollTimesNanos(requestId);
        long minimum = minimumDelay.toNanos();
        for (int i = 1; i < times.size(); i++) {
            long actual = times.get(i) - times.get(i - 1);
            if (actual < minimum) {
                throw new WireAssertionError(
                        "["
                                + scenario
                                + "] getRequestTracker polls for request "
                                + requestId
                                + " were only "
                                + actual
                                + "ns apart, expected at least "
                                + minimum
                                + "ns");
            }
        }
    }

    /** Asserts no request at all reached the fixture, for calls that must fail before any I/O. */
    public static void silent(String scenario, MockVcfAutomation mock) {
        List<MockVcfAutomation.Recorded> actual = mock.log();
        if (!actual.isEmpty()) {
            throw new WireAssertionError(
                    "[" + scenario + "] expected no request but observed:\n" + renderActual(actual));
        }
    }

    /** Asserts a diagnostic never repeats a credential. */
    public static void withoutSecrets(String scenario, String text, String... secrets) {
        if (text == null) {
            return;
        }
        for (String secret : secrets) {
            if (!secret.isEmpty() && text.contains(secret)) {
                throw new WireAssertionError("[" + scenario + "] a diagnostic leaked a credential");
            }
        }
    }

    private static String renderExpected(List<Expect> expected) {
        List<String> lines = new ArrayList<>();
        for (Expect e : expected) {
            lines.add(
                    "    "
                            + e.method()
                            + " "
                            + e.path()
                            + (e.query() == null ? "" : "?" + e.query())
                            + (e.body() == null ? "" : " " + e.body()));
        }
        return String.join("\n", lines);
    }

    private static String renderActual(List<MockVcfAutomation.Recorded> actual) {
        List<String> lines = new ArrayList<>();
        for (MockVcfAutomation.Recorded r : actual) {
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

    private static String offContractLines(List<MockVcfAutomation.Recorded> actual) {
        List<String> lines = new ArrayList<>();
        for (MockVcfAutomation.Recorded r : actual) {
            if (r.operationId() == null) {
                lines.add(r.method() + " " + r.path());
            }
        }
        return String.join(", ", lines);
    }
}
